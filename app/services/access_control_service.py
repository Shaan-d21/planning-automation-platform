"""Durable users, roles, authentication, and authorization services."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database.engine import (
    DatabaseTarget,
    database_for,
    upsert_statement,
    utc_datetime,
)
from app.infrastructure.database.schema import (
    authentication_events,
    external_identities,
    platform_role_permissions,
    platform_roles,
    platform_user_roles,
    platform_users,
)

from app.models.access_control import (
    Permission,
    RoleCode,
    RoleDefinition,
    UserAccount,
)
from app.utils.exceptions import AccessControlError


_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{2,79}$")
_PASSWORD_SCHEME = "scrypt-v1"
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_MAX_FAILED_LOGINS = 5
_LOCKOUT_DURATION = timedelta(minutes=15)
_MAX_ORACLE_CREDENTIAL_FAILURES = 3
_MAX_ORACLE_IP_FAILURES = 15


ROLE_DEFINITIONS = (
    RoleDefinition(
        code=RoleCode.SERVICE_ADMINISTRATOR,
        name="Service Administrator",
        description=(
            "Administers the platform, users, Planning cycles, schedules, "
            "and governed Oracle EPM operations."
        ),
        permissions=frozenset(Permission),
    ),
    RoleDefinition(
        code=RoleCode.POWER_USER,
        name="Power User",
        description=(
            "Runs approved Planning processes and operations, reviews data, "
            "and monitors execution without administering users or design."
        ),
        permissions=frozenset(
            {
                Permission.PROCESS_RUN,
                Permission.OPERATION_EXECUTE,
                Permission.USER_VARIABLE_UPDATE,
                Permission.DATA_REVIEW,
                Permission.REPORT_GENERATE,
                Permission.HISTORY_VIEW,
                Permission.AGENT_USE,
            }
        ),
    ),
    RoleDefinition(
        code=RoleCode.USER,
        name="User",
        description=(
            "Completes assigned Planning work, reviews authorized data, runs "
            "approved processes, and generates reports."
        ),
        permissions=frozenset(
            {
                Permission.PROCESS_RUN,
                Permission.USER_VARIABLE_UPDATE,
                Permission.DATA_REVIEW,
                Permission.REPORT_GENERATE,
                Permission.AGENT_USE,
            }
        ),
    ),
    RoleDefinition(
        code=RoleCode.VIEWER,
        name="Viewer",
        description=(
            "Consumes authorized Planning reports and read-only agent "
            "guidance without operational controls."
        ),
        permissions=frozenset(
            {
                Permission.REPORT_GENERATE,
                Permission.AGENT_USE,
            }
        ),
    ),
)


class PasswordHasher:
    """Versioned, memory-hard password hashing using standard-library scrypt."""

    @staticmethod
    def hash(password: str) -> str:
        """Validate and hash a new password with a random salt."""
        PasswordHasher.validate(password)
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=32,
        )
        return "$".join(
            (
                _PASSWORD_SCHEME,
                str(_SCRYPT_N),
                str(_SCRYPT_R),
                str(_SCRYPT_P),
                salt.hex(),
                digest.hex(),
            )
        )

    @staticmethod
    def verify(password: str, encoded: str | None) -> bool:
        """Verify a password without exposing parsing failures."""
        if not encoded:
            return False
        try:
            scheme, n, r, p, salt_hex, digest_hex = encoded.split("$", 5)
            if scheme != _PASSWORD_SCHEME:
                return False
            expected = bytes.fromhex(digest_hex)
            actual = hashlib.scrypt(
                password.encode("utf-8"),
                salt=bytes.fromhex(salt_hex),
                n=int(n),
                r=int(r),
                p=int(p),
                dklen=len(expected),
            )
        except (TypeError, ValueError):
            return False
        return hmac.compare_digest(actual, expected)

    @staticmethod
    def validate(password: str) -> None:
        """Apply a passphrase-friendly minimum strength policy."""
        if len(password) < 12:
            raise AccessControlError(
                "Password must contain at least 12 characters."
            )
        if len(password) > 256:
            raise AccessControlError(
                "Password cannot exceed 256 characters."
            )


class AccessControlService:
    """Own durable platform identities independently of Oracle credentials."""

    def __init__(self, database_target: DatabaseTarget) -> None:
        self._database = database_for(database_target)
        self._seed_roles()
        self._dummy_hash = PasswordHasher.hash("dummy timing password")

    @property
    def requires_bootstrap(self) -> bool:
        """Return whether the first administrator still needs creation."""
        with self._database.connect() as connection:
            count = connection.execute(
                select(func.count()).select_from(platform_users)
            ).scalar_one()
        return int(count) == 0

    def roles(self) -> tuple[RoleDefinition, ...]:
        """Return the stable role catalog."""
        return ROLE_DEFINITIONS

    def bootstrap_administrator(
        self,
        *,
        username: str,
        display_name: str,
        email: str | None,
        password: str,
        ip_address: str | None = None,
    ) -> UserAccount:
        """Atomically create the only permitted first-run administrator."""
        normalized = self._validated_identity(username, display_name, email)
        password_hash = PasswordHasher.hash(password)
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(select(func.pg_advisory_xact_lock(8_237_401)))
            if int(
                connection.execute(
                    select(func.count()).select_from(platform_users)
                ).scalar_one()
            ):
                raise AccessControlError(
                    "Initial administrator setup has already been completed."
                )
            user_id = int(
                connection.execute(
                    insert(platform_users)
                    .values(
                        username=normalized[0],
                        display_name=normalized[1],
                        email=normalized[2],
                        password_hash=password_hash,
                        is_active=True,
                        created_at=now,
                        updated_at=now,
                    )
                    .returning(platform_users.c.user_id)
                ).scalar_one()
            )
            self._assign_roles(
                connection,
                user_id,
                (RoleCode.SERVICE_ADMINISTRATOR,),
                assigned_by_user_id=user_id,
            )
            self._record_event(
                connection,
                event_type="BOOTSTRAP_ADMIN_CREATED",
                username=normalized[0],
                actor_user_id=user_id,
                success=True,
                ip_address=ip_address,
            )
        return self.require_user(user_id)

    def authenticate(
        self,
        username: str,
        password: str,
        *,
        ip_address: str | None = None,
    ) -> UserAccount | None:
        """Authenticate an active platform user and audit the attempt."""
        normalized_username = str(username).strip()
        with self._database.begin() as connection:
            row = connection.execute(
                select(platform_users)
                .where(
                    func.lower(platform_users.c.username)
                    == normalized_username.casefold()
                )
                .with_for_update()
            ).mappings().one_or_none()
            locked_until = (
                utc_datetime(row["locked_until"])
                if row is not None
                else None
            )
            locked = bool(locked_until and locked_until > datetime.now(UTC))
            password_valid = PasswordHasher.verify(
                password,
                row["password_hash"] if row is not None else self._dummy_hash,
            )
            valid = bool(
                row is not None
                and bool(row["is_active"])
                and not locked
                and password_valid
            )
            self._record_event(
                connection,
                event_type="LOGIN",
                username=normalized_username,
                actor_user_id=(int(row["user_id"]) if valid else None),
                success=valid,
                ip_address=ip_address,
            )
            if not valid:
                if row is not None and bool(row["is_active"]) and not locked:
                    failed_count = int(row["failed_login_count"] or 0) + 1
                    new_lock = (
                        datetime.now(UTC) + _LOCKOUT_DURATION
                        if failed_count >= _MAX_FAILED_LOGINS
                        else None
                    )
                    connection.execute(
                        update(platform_users)
                        .where(platform_users.c.user_id == int(row["user_id"]))
                        .values(
                            failed_login_count=failed_count,
                            locked_until=new_lock,
                            updated_at=datetime.now(UTC),
                        )
                    )
                return None
            now = datetime.now(UTC)
            connection.execute(
                update(platform_users)
                .where(platform_users.c.user_id == int(row["user_id"]))
                .values(
                    last_login_at=now,
                    updated_at=now,
                    failed_login_count=0,
                    locked_until=None,
                )
            )
            user_id = int(row["user_id"])
        return self.require_user(user_id)

    def get_user(self, user_id: int) -> UserAccount | None:
        """Resolve one user with effective roles and permissions."""
        with self._database.connect() as connection:
            row = connection.execute(
                select(platform_users).where(platform_users.c.user_id == user_id)
            ).mappings().one_or_none()
            if row is None:
                return None
            roles = tuple(
                RoleCode(str(item))
                for item in connection.execute(
                    select(platform_roles.c.code)
                    .join(
                        platform_user_roles,
                        platform_user_roles.c.role_id == platform_roles.c.role_id,
                    )
                    .where(platform_user_roles.c.user_id == user_id)
                    .order_by(func.lower(platform_roles.c.name))
                ).scalars().all()
            )
            permissions = frozenset(
                Permission(str(item))
                for item in connection.execute(
                    select(platform_role_permissions.c.permission_code)
                    .join(
                        platform_user_roles,
                        platform_user_roles.c.role_id
                        == platform_role_permissions.c.role_id,
                    )
                    .where(platform_user_roles.c.user_id == user_id)
                    .distinct()
                ).scalars().all()
            )
        return UserAccount(
            user_id=int(row["user_id"]),
            username=str(row["username"]),
            display_name=str(row["display_name"]),
            email=(str(row["email"]) if row["email"] else None),
            active=bool(row["is_active"]),
            roles=roles,
            permissions=permissions,
            created_at=utc_datetime(row["created_at"]),
            updated_at=utc_datetime(row["updated_at"]),
            last_login_at=utc_datetime(row["last_login_at"]),
        )

    def require_user(self, user_id: int) -> UserAccount:
        user = self.get_user(user_id)
        if user is None:
            raise AccessControlError(f"Platform user {user_id} was not found.")
        return user

    def list_users(self) -> tuple[UserAccount, ...]:
        """List every retained identity, including deactivated users."""
        with self._database.connect() as connection:
            ids = tuple(
                int(value)
                for value in connection.execute(
                    select(platform_users.c.user_id).order_by(
                        platform_users.c.is_active.desc(),
                        func.lower(platform_users.c.display_name),
                    )
                ).scalars().all()
            )
        return tuple(self.require_user(user_id) for user_id in ids)

    def authentication_sources(self) -> dict[int, str]:
        """Classify accounts without exposing password or provider details."""
        with self._database.connect() as connection:
            rows = connection.execute(
                select(
                    platform_users.c.user_id,
                    platform_users.c.password_hash,
                    external_identities.c.external_identity_id,
                ).outerjoin(
                    external_identities,
                    external_identities.c.user_id == platform_users.c.user_id,
                )
            ).all()
        sources: dict[int, str] = {}
        for user_id, password_hash, external_identity_id in rows:
            linked = external_identity_id is not None
            local = password_hash is not None
            sources[int(user_id)] = (
                "ORACLE_AND_LOCAL"
                if linked and local
                else "ORACLE_LINKED"
                if linked
                else "LOCAL_RECOVERY"
            )
        return sources

    def create_user(
        self,
        *,
        username: str,
        display_name: str,
        email: str | None,
        password: str,
        roles: tuple[RoleCode, ...],
        actor_user_id: int,
    ) -> UserAccount:
        """Create an active user with one or more approved roles."""
        normalized = self._validated_identity(username, display_name, email)
        normalized_roles = self._validated_roles(roles)
        password_hash = PasswordHasher.hash(password)
        now = datetime.now(UTC)
        try:
            with self._database.begin() as connection:
                user_id = int(
                    connection.execute(
                        insert(platform_users)
                        .values(
                            username=normalized[0],
                            display_name=normalized[1],
                            email=normalized[2],
                            password_hash=password_hash,
                            is_active=True,
                            created_at=now,
                            updated_at=now,
                        )
                        .returning(platform_users.c.user_id)
                    ).scalar_one()
                )
                self._assign_roles(
                    connection,
                    user_id,
                    normalized_roles,
                    assigned_by_user_id=actor_user_id,
                )
                self._record_event(
                    connection,
                    event_type="USER_CREATED",
                    username=normalized[0],
                    actor_user_id=actor_user_id,
                    success=True,
                )
        except IntegrityError as exc:
            raise AccessControlError(
                f"Username '{normalized[0]}' already exists."
            ) from exc
        return self.require_user(user_id)

    def update_user(
        self,
        user_id: int,
        *,
        display_name: str,
        email: str | None,
        active: bool,
        roles: tuple[RoleCode, ...],
        actor_user_id: int,
    ) -> UserAccount:
        """Update profile, activation, and role assignments safely."""
        current = self.require_user(user_id)
        self._reject_linked_profile_management(user_id)
        _, normalized_name, normalized_email = self._validated_identity(
            current.username,
            display_name,
            email,
        )
        normalized_roles = self._validated_roles(roles)
        now = datetime.now(UTC)
        try:
            with self._database.begin() as connection:
                current_admin = connection.execute(
                    select(platform_roles.c.role_id)
                    .join(
                        platform_user_roles,
                        platform_user_roles.c.role_id == platform_roles.c.role_id,
                    )
                    .where(
                        platform_user_roles.c.user_id == user_id,
                        platform_roles.c.code
                        == RoleCode.SERVICE_ADMINISTRATOR.value,
                    )
                ).first()
                is_current_admin = current_admin is not None
                if (
                    is_current_admin
                    and (
                        not active
                        or RoleCode.SERVICE_ADMINISTRATOR not in normalized_roles
                    )
                    and self._active_administrator_count(connection) <= 1
                ):
                    raise AccessControlError(
                        "The last active Service Administrator cannot be removed."
                    )
                connection.execute(
                    update(platform_users)
                    .where(platform_users.c.user_id == user_id)
                    .values(
                        display_name=normalized_name,
                        email=normalized_email,
                        is_active=active,
                        updated_at=now,
                    )
                )
                connection.execute(
                    delete(platform_user_roles).where(
                        platform_user_roles.c.user_id == user_id
                    )
                )
                self._assign_roles(
                    connection,
                    user_id,
                    normalized_roles,
                    assigned_by_user_id=actor_user_id,
                )
                self._record_event(
                    connection,
                    event_type="USER_UPDATED",
                    username=current.username,
                    actor_user_id=actor_user_id,
                    success=True,
                    details={
                        "active": active,
                        "roles": [role.value for role in normalized_roles],
                    },
                )
        except IntegrityError as exc:
            raise AccessControlError(
                "The requested email address is already assigned to another user."
            ) from exc
        return self.require_user(user_id)

    def reset_password(
        self,
        user_id: int,
        password: str,
        *,
        actor_user_id: int,
    ) -> None:
        """Replace a password without ever returning a credential value."""
        user = self.require_user(user_id)
        self._reject_linked_profile_management(user_id)
        password_hash = PasswordHasher.hash(password)
        with self._database.begin() as connection:
            connection.execute(
                update(platform_users)
                .where(platform_users.c.user_id == user_id)
                .values(
                    password_hash=password_hash,
                    updated_at=datetime.now(UTC),
                )
            )
            self._record_event(
                connection,
                event_type="PASSWORD_RESET",
                username=user.username,
                actor_user_id=actor_user_id,
                success=True,
            )

    def _reject_linked_profile_management(self, user_id: int) -> None:
        """Keep externally governed profiles passwordless and mapping-managed."""
        with self._database.connect() as connection:
            linked = connection.execute(
                select(external_identities.c.external_identity_id).where(
                    external_identities.c.user_id == user_id
                )
            ).first()
        if linked is not None:
            raise AccessControlError(
                "This Oracle-linked profile is managed through synchronized "
                "role mappings. Its role and password cannot be changed as a "
                "local account."
            )

    def record_logout(
        self,
        user: UserAccount,
        *,
        ip_address: str | None = None,
    ) -> None:
        """Record a successful platform sign-out."""
        with self._database.begin() as connection:
            self._record_event(
                connection,
                event_type="LOGOUT",
                username=user.username,
                actor_user_id=user.user_id,
                success=True,
                ip_address=ip_address,
            )

    def record_external_login(
        self,
        user_id: int,
        *,
        provider_code: str,
        authentication_method: str,
        ip_address: str | None = None,
    ) -> UserAccount:
        """Record a successful externally validated login without a password."""
        user = self.require_user(user_id)
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            connection.execute(
                update(platform_users)
                .where(platform_users.c.user_id == user_id)
                .values(
                    failed_login_count=0,
                    locked_until=None,
                    last_login_at=now,
                    updated_at=now,
                )
            )
            self._record_event(
                connection,
                event_type="ORACLE_LOGIN_SUCCESS",
                username=user.username,
                actor_user_id=user_id,
                success=True,
                ip_address=ip_address,
                details={
                    "provider_code": provider_code,
                    "authentication_method": authentication_method,
                },
            )
        return self.require_user(user_id)

    def record_external_login_failure(
        self,
        username: str,
        *,
        reason: str,
        credential_failure: bool = False,
        ip_address: str | None = None,
    ) -> None:
        """Audit an external-login rejection without storing credentials."""
        normalized = str(username).strip()[:80] or "unknown"
        with self._database.begin() as connection:
            self._record_event(
                connection,
                event_type=(
                    "ORACLE_CREDENTIAL_REJECTED"
                    if credential_failure
                    else "ORACLE_LOGIN_DENIED"
                ),
                username=normalized,
                actor_user_id=None,
                success=False,
                ip_address=ip_address,
                details={"reason": str(reason).strip()[:120]},
            )

    def external_login_rate_limited(
        self,
        username: str,
        *,
        ip_address: str | None = None,
    ) -> bool:
        """Protect Oracle accounts from repeated credential verification."""
        normalized = str(username).strip()[:80].casefold()
        cutoff = datetime.now(UTC) - _LOCKOUT_DURATION
        with self._database.connect() as connection:
            username_failures = int(
                connection.execute(
                    select(func.count())
                    .select_from(authentication_events)
                    .where(
                        authentication_events.c.event_type
                        == "ORACLE_CREDENTIAL_REJECTED",
                        authentication_events.c.occurred_at >= cutoff,
                        func.lower(authentication_events.c.username_snapshot)
                        == normalized,
                    )
                ).scalar_one()
            )
            if username_failures >= _MAX_ORACLE_CREDENTIAL_FAILURES:
                return True
            if not ip_address:
                return False
            ip_failures = int(
                connection.execute(
                    select(func.count())
                    .select_from(authentication_events)
                    .where(
                        authentication_events.c.event_type
                        == "ORACLE_CREDENTIAL_REJECTED",
                        authentication_events.c.occurred_at >= cutoff,
                        authentication_events.c.ip_address
                        == str(ip_address)[:45],
                    )
                ).scalar_one()
            )
            return ip_failures >= _MAX_ORACLE_IP_FAILURES

    def record_external_login_throttled(
        self,
        username: str,
        *,
        ip_address: str | None = None,
    ) -> None:
        """Audit a protected request without extending the throttle window."""
        normalized = str(username).strip()[:80] or "unknown"
        with self._database.begin() as connection:
            self._record_event(
                connection,
                event_type="ORACLE_LOGIN_THROTTLED",
                username=normalized,
                actor_user_id=None,
                success=False,
                ip_address=ip_address,
                details={"reason": "TOO_MANY_CREDENTIAL_FAILURES"},
            )

    def _seed_roles(self) -> None:
        with self._database.begin() as connection:
            for role in ROLE_DEFINITIONS:
                connection.execute(
                    upsert_statement(
                        connection,
                        platform_roles,
                        {
                            "code": role.code.value,
                            "name": role.name,
                            "description": role.description,
                            "is_system": True,
                        },
                        index_elements=("code",),
                        update_columns=("name", "description", "is_system"),
                    )
                )
                role_id = int(
                    connection.execute(
                        select(platform_roles.c.role_id).where(
                            platform_roles.c.code == role.code.value
                        )
                    ).scalar_one()
                )
                connection.execute(
                    delete(platform_role_permissions).where(
                        platform_role_permissions.c.role_id == role_id
                    )
                )
                connection.execute(
                    insert(platform_role_permissions),
                    [
                        {
                            "role_id": role_id,
                            "permission_code": permission.value,
                        }
                        for permission in sorted(
                            role.permissions,
                            key=lambda item: item.value,
                        )
                    ],
                )

    @staticmethod
    def _validated_identity(
        username: str,
        display_name: str,
        email: str | None,
    ) -> tuple[str, str, str | None]:
        normalized_username = str(username).strip()
        normalized_name = " ".join(str(display_name).split())
        normalized_email = str(email).strip() if email else None
        if not _USERNAME_PATTERN.fullmatch(normalized_username):
            raise AccessControlError(
                "Username must be 3-80 characters and use only letters, "
                "numbers, dots, underscores, hyphens, or @."
            )
        if not normalized_name or len(normalized_name) > 120:
            raise AccessControlError(
                "Display name must contain 1-120 characters."
            )
        if normalized_email and (
            len(normalized_email) > 254
            or "@" not in normalized_email
            or normalized_email.startswith("@")
            or normalized_email.endswith("@")
        ):
            raise AccessControlError("Enter a valid email address.")
        return normalized_username, normalized_name, normalized_email

    @staticmethod
    def _validated_roles(
        roles: tuple[RoleCode, ...],
    ) -> tuple[RoleCode, ...]:
        normalized = tuple(dict.fromkeys(RoleCode(role) for role in roles))
        if len(normalized) != 1:
            raise AccessControlError("Select exactly one platform role.")
        return normalized

    @staticmethod
    def _assign_roles(
        connection,
        user_id: int,
        roles: tuple[RoleCode, ...],
        *,
        assigned_by_user_id: int | None,
    ) -> None:
        now = datetime.now(UTC)
        for role in roles:
            row = connection.execute(
                select(platform_roles.c.role_id).where(
                    platform_roles.c.code == role.value
                )
            ).scalar_one_or_none()
            if row is None:
                raise AccessControlError(f"Role '{role.value}' was not found.")
            connection.execute(
                insert(platform_user_roles).values(
                    user_id=user_id,
                    role_id=int(row),
                    assigned_at=now,
                    assigned_by_user_id=assigned_by_user_id,
                )
            )

    @staticmethod
    def _active_administrator_count(
        connection,
    ) -> int:
        row = connection.execute(
            select(func.count(func.distinct(platform_users.c.user_id)))
            .join(
                platform_user_roles,
                platform_user_roles.c.user_id == platform_users.c.user_id,
            )
            .join(
                platform_roles,
                platform_roles.c.role_id == platform_user_roles.c.role_id,
            )
            .where(
                platform_users.c.is_active.is_(True),
                platform_roles.c.code
                == RoleCode.SERVICE_ADMINISTRATOR.value,
            )
        ).scalar_one()
        return int(row)

    @staticmethod
    def _record_event(
        connection,
        *,
        event_type: str,
        username: str,
        actor_user_id: int | None,
        success: bool,
        ip_address: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        normalized_details = {
            str(key): (
                value.value if hasattr(value, "value") else value
            )
            for key, value in (details or {}).items()
        }
        connection.execute(
            insert(authentication_events).values(
                event_type=event_type,
                username_snapshot=username,
                actor_user_id=actor_user_id,
                success=success,
                ip_address=ip_address,
                occurred_at=datetime.now(UTC),
                details=normalized_details,
            )
        )
