"""Governed provisioning of passwordless Oracle-linked platform profiles."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

from sqlalchemy import delete, func, insert, select, update

from app.infrastructure.database.engine import DatabaseTarget, database_for
from app.infrastructure.database.schema import (
    authentication_events,
    external_entitlements,
    external_identities,
    external_identity_entitlements,
    identity_providers,
    identity_role_mappings,
    platform_roles,
    platform_user_roles,
    platform_users,
)
from app.models.access_control import RoleCode
from app.models.identity import (
    IdentityProvisioningAction,
    IdentityProvisioningEntry,
    IdentityProvisioningPreview,
    IdentityProvisioningResult,
)
from app.utils.exceptions import (
    IdentitySnapshotChangedError,
    IdentitySynchronizationError,
)


_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{2,79}$")
_ROLE_PRIORITY = {
    RoleCode.VIEWER.value: 10,
    RoleCode.USER.value: 20,
    RoleCode.POWER_USER.value: 30,
    RoleCode.SERVICE_ADMINISTRATOR.value: 40,
}


class FederatedProvisioningService:
    """Derive and apply linked profiles from synchronized entitlements."""

    def __init__(self, database_target: DatabaseTarget) -> None:
        self._database = database_for(database_target)

    def preview(self, provider_code: str) -> IdentityProvisioningPreview:
        """Return an immutable plan without changing platform accounts."""
        with self._database.connect() as connection:
            provider_id, normalized_code = self._provider(connection, provider_code)
            return self._build_preview(connection, provider_id, normalized_code)

    def provision_identity(
        self,
        provider_code: str,
        subject: str,
        *,
        actor_user_id: int | None = None,
    ) -> int:
        """Create or refresh one mapped linked profile during Oracle login."""
        normalized_subject = str(subject).strip()
        if not normalized_subject or len(normalized_subject) > 255:
            raise IdentitySynchronizationError(
                "A valid synchronized Oracle identity is required."
            )
        with self._database.begin() as connection:
            provider_id, normalized_code = self._provider(
                connection,
                provider_code,
            )
            if connection.dialect.name == "postgresql":
                connection.execute(
                    select(func.pg_advisory_xact_lock(9_100_000 + provider_id))
                )
            identity_row = connection.execute(
                select(
                    external_identities.c.external_identity_id,
                    external_identities.c.user_id,
                ).where(
                    external_identities.c.provider_id == provider_id,
                    external_identities.c.subject == normalized_subject,
                )
            ).mappings().one_or_none()
            if identity_row is None:
                raise IdentitySynchronizationError(
                    "The authenticated Oracle identity was not synchronized."
                )
            identity_id = int(identity_row["external_identity_id"])
            preview = self._build_preview(
                connection,
                provider_id,
                normalized_code,
                lock=True,
            )
            entry = next(
                (
                    item
                    for item in preview.entries
                    if item.external_identity_id == identity_id
                ),
                None,
            )
            if entry is None:
                raise IdentitySynchronizationError(
                    "The authenticated Oracle identity could not be evaluated."
                )
            if entry.action == IdentityProvisioningAction.SKIP_UNMAPPED:
                raise IdentitySynchronizationError(
                    "Your Oracle account is valid, but none of its current "
                    "roles or groups is mapped to platform access."
                )
            if entry.action == IdentityProvisioningAction.CONFLICT:
                raise IdentitySynchronizationError(entry.explanation)
            if entry.action == IdentityProvisioningAction.DEACTIVATE:
                raise IdentitySynchronizationError(
                    "Your linked platform profile is no longer eligible for access."
                )
            if entry.target_role is None:
                raise IdentitySynchronizationError(
                    "The Oracle identity does not resolve to an approved platform role."
                )
            role_id = connection.execute(
                select(platform_roles.c.role_id).where(
                    platform_roles.c.code == entry.target_role
                )
            ).scalar_one_or_none()
            if role_id is None:
                raise IdentitySynchronizationError(
                    "The mapped platform role is unavailable."
                )
            now = datetime.now(UTC)
            if entry.action == IdentityProvisioningAction.CREATE:
                user_id = int(
                    connection.execute(
                        insert(platform_users)
                        .values(
                            username=entry.username,
                            display_name=entry.display_name,
                            email=entry.email,
                            password_hash=None,
                            is_active=True,
                            created_at=now,
                            updated_at=now,
                        )
                        .returning(platform_users.c.user_id)
                    ).scalar_one()
                )
                connection.execute(
                    update(external_identities)
                    .where(
                        external_identities.c.external_identity_id
                        == entry.external_identity_id
                    )
                    .values(user_id=user_id, updated_at=now)
                )
                event_type = "LINKED_ORACLE_PROFILE_CREATED"
            else:
                if entry.user_id is None:
                    raise IdentitySynchronizationError(
                        "The linked Oracle profile has no platform user."
                    )
                user_id = entry.user_id
                reused_profile = identity_row["user_id"] is None
                connection.execute(
                    update(external_identities)
                    .where(
                        external_identities.c.external_identity_id
                        == entry.external_identity_id
                    )
                    .values(user_id=user_id, updated_at=now)
                )
                if entry.action == IdentityProvisioningAction.UPDATE:
                    connection.execute(
                        update(platform_users)
                        .where(platform_users.c.user_id == user_id)
                        .values(
                            display_name=entry.display_name,
                            email=entry.email,
                            is_active=True,
                            updated_at=now,
                        )
                    )
                    event_type = (
                        "LINKED_ORACLE_PROFILE_REUSED"
                        if reused_profile
                        else "LINKED_ORACLE_PROFILE_UPDATED"
                    )
                else:
                    event_type = "LINKED_ORACLE_PROFILE_CONFIRMED"
            self._replace_role(
                connection,
                user_id,
                int(role_id),
                actor_user_id,
                now,
            )
            self._audit(
                connection,
                event_type,
                entry,
                user_id,
                actor_user_id,
                now,
            )
            return user_id

    def apply(
        self,
        provider_code: str,
        expected_checksum: str,
        *,
        actor_user_id: int,
    ) -> tuple[IdentityProvisioningResult, IdentityProvisioningPreview]:
        """Apply safe entries from the exact reviewed state in one transaction."""
        checksum = str(expected_checksum).strip().casefold()
        if len(checksum) != 64 or any(
            character not in "0123456789abcdef" for character in checksum
        ):
            raise IdentitySynchronizationError(
                "A valid provisioning preview checksum is required."
            )
        created = updated = deactivated = unchanged = unmapped = conflicts = 0
        with self._database.begin() as connection:
            provider_id, normalized_code = self._provider(connection, provider_code)
            if connection.dialect.name == "postgresql":
                connection.execute(
                    select(func.pg_advisory_xact_lock(9_100_000 + provider_id))
                )
            preview = self._build_preview(
                connection,
                provider_id,
                normalized_code,
                lock=True,
            )
            if preview.checksum != checksum:
                raise IdentitySnapshotChangedError(
                    "Identity mappings or account data changed after the preview. "
                    "Review the refreshed provisioning plan before applying it."
                )
            role_ids = {
                str(code): int(role_id)
                for code, role_id in connection.execute(
                    select(platform_roles.c.code, platform_roles.c.role_id)
                ).all()
            }
            now = datetime.now(UTC)
            for entry in preview.entries:
                if entry.action == IdentityProvisioningAction.CONFLICT:
                    conflicts += 1
                    continue
                if entry.action == IdentityProvisioningAction.SKIP_UNMAPPED:
                    unmapped += 1
                    continue
                if entry.action == IdentityProvisioningAction.UNCHANGED:
                    unchanged += 1
                    continue
                if entry.action == IdentityProvisioningAction.CREATE:
                    if entry.target_role is None:
                        raise IdentitySynchronizationError(
                            "Provisioning plan contains a new user without a role."
                        )
                    user_id = int(
                        connection.execute(
                            insert(platform_users)
                            .values(
                                username=entry.username,
                                display_name=entry.display_name,
                                email=entry.email,
                                password_hash=None,
                                is_active=True,
                                created_at=now,
                                updated_at=now,
                            )
                            .returning(platform_users.c.user_id)
                        ).scalar_one()
                    )
                    self._replace_role(
                        connection,
                        user_id,
                        role_ids[entry.target_role],
                        actor_user_id,
                        now,
                    )
                    connection.execute(
                        update(external_identities)
                        .where(
                            external_identities.c.external_identity_id
                            == entry.external_identity_id
                        )
                        .values(user_id=user_id, updated_at=now)
                    )
                    self._audit(
                        connection,
                        "FEDERATED_USER_CREATED",
                        entry,
                        user_id,
                        actor_user_id,
                        now,
                    )
                    created += 1
                    continue
                if entry.user_id is None:
                    raise IdentitySynchronizationError(
                        "Provisioning plan references an unlinked existing user."
                    )
                if entry.action == IdentityProvisioningAction.DEACTIVATE:
                    connection.execute(
                        update(platform_users)
                        .where(platform_users.c.user_id == entry.user_id)
                        .values(is_active=False, updated_at=now)
                    )
                    self._audit(
                        connection,
                        "FEDERATED_USER_DEACTIVATED",
                        entry,
                        entry.user_id,
                        actor_user_id,
                        now,
                    )
                    deactivated += 1
                    continue
                if entry.target_role is None:
                    raise IdentitySynchronizationError(
                        "Provisioning plan contains an update without a role."
                    )
                linked_existing_profile = bool(
                    connection.execute(
                        update(external_identities)
                        .where(
                            external_identities.c.external_identity_id
                            == entry.external_identity_id,
                            external_identities.c.user_id.is_(None),
                        )
                        .values(user_id=entry.user_id, updated_at=now)
                    ).rowcount
                )
                connection.execute(
                    update(platform_users)
                    .where(platform_users.c.user_id == entry.user_id)
                    .values(
                        display_name=entry.display_name,
                        email=entry.email,
                        is_active=True,
                        updated_at=now,
                    )
                )
                self._replace_role(
                    connection,
                    entry.user_id,
                    role_ids[entry.target_role],
                    actor_user_id,
                    now,
                )
                self._audit(
                    connection,
                    (
                        "FEDERATED_USER_REUSED"
                        if linked_existing_profile
                        else "FEDERATED_USER_UPDATED"
                    ),
                    entry,
                    entry.user_id,
                    actor_user_id,
                    now,
                )
                updated += 1
        return (
            IdentityProvisioningResult(
                created=created,
                updated=updated,
                deactivated=deactivated,
                unchanged=unchanged,
                unmapped=unmapped,
                conflicts=conflicts,
            ),
            preview,
        )

    def _build_preview(
        self,
        connection,
        provider_id: int,
        provider_code: str,
        *,
        lock: bool = False,
    ) -> IdentityProvisioningPreview:
        identity_statement = select(external_identities).where(
            external_identities.c.provider_id == provider_id
        )
        if lock and connection.dialect.name == "postgresql":
            identity_statement = identity_statement.with_for_update()
        identities = connection.execute(identity_statement).mappings().all()
        mappings: dict[int, list[tuple[str, str]]] = {}
        for external_identity_id, entitlement_name, role_code in connection.execute(
            select(
                external_identity_entitlements.c.external_identity_id,
                external_entitlements.c.display_name,
                platform_roles.c.code,
            )
            .select_from(external_identity_entitlements)
            .join(
                external_entitlements,
                external_entitlements.c.entitlement_id
                == external_identity_entitlements.c.entitlement_id,
            )
            .join(
                identity_role_mappings,
                identity_role_mappings.c.entitlement_id
                == external_entitlements.c.entitlement_id,
            )
            .join(
                platform_roles,
                platform_roles.c.role_id == identity_role_mappings.c.role_id,
            )
            .where(
                external_entitlements.c.provider_id == provider_id,
                external_entitlements.c.is_active.is_(True),
                identity_role_mappings.c.is_enabled.is_(True),
            )
        ).all():
            mappings.setdefault(int(external_identity_id), []).append(
                (str(entitlement_name), str(role_code))
            )

        platform_statement = select(platform_users)
        if lock and connection.dialect.name == "postgresql":
            platform_statement = platform_statement.with_for_update()
        platform_rows = {
            int(row["user_id"]): row
            for row in connection.execute(platform_statement).mappings().all()
        }
        username_owners = {
            str(row["username"]).casefold(): int(row["user_id"])
            for row in platform_rows.values()
        }
        email_owners = {
            str(row["email"]).casefold(): int(row["user_id"])
            for row in platform_rows.values()
            if row["email"]
        }
        current_provider = connection.execute(
            select(identity_providers).where(
                identity_providers.c.provider_id == provider_id
            )
        ).mappings().one()
        managed_links = connection.execute(
            select(
                external_identities.c.user_id,
                external_identities.c.subject,
                external_identities.c.username,
                external_identities.c.provider_id,
                identity_providers.c.provider_type,
                identity_providers.c.issuer_url,
                identity_providers.c.safe_configuration,
            )
            .select_from(external_identities)
            .join(
                identity_providers,
                identity_providers.c.provider_id
                == external_identities.c.provider_id,
            )
            .where(external_identities.c.user_id.is_not(None))
        ).mappings().all()
        current_provider_linked_users = {
            int(identity["user_id"])
            for identity in identities
            if identity["user_id"] is not None
        }
        role_by_user: dict[int, tuple[str, ...]] = {}
        for user_id, role_code in connection.execute(
            select(platform_user_roles.c.user_id, platform_roles.c.code).join(
                platform_roles,
                platform_roles.c.role_id == platform_user_roles.c.role_id,
            )
        ).all():
            role_by_user.setdefault(int(user_id), ())
            role_by_user[int(user_id)] += (str(role_code),)

        external_username_counts: dict[str, int] = {}
        external_email_counts: dict[str, int] = {}
        for identity in identities:
            username_key = str(identity["username"]).casefold()
            external_username_counts[username_key] = (
                external_username_counts.get(username_key, 0) + 1
            )
            if identity["email"]:
                email_key = str(identity["email"]).casefold()
                external_email_counts[email_key] = (
                    external_email_counts.get(email_key, 0) + 1
                )

        entries: list[IdentityProvisioningEntry] = []
        state: list[dict[str, object]] = []
        for identity in identities:
            external_identity_id = int(identity["external_identity_id"])
            linked_user_id = (
                int(identity["user_id"])
                if identity["user_id"] is not None
                else None
            )
            matched = mappings.get(external_identity_id, [])
            target_role = self._highest_role(role for _, role in matched)
            reused_managed_profile = False
            resolved_user_id = linked_user_id
            if resolved_user_id is None and target_role is not None:
                resolved_user_id = self._reusable_managed_profile(
                    identity,
                    current_provider,
                    username_owners,
                    email_owners,
                    platform_rows,
                    managed_links,
                    current_provider_linked_users,
                )
                reused_managed_profile = resolved_user_id is not None
            platform_row = (
                platform_rows.get(resolved_user_id)
                if resolved_user_id is not None
                else None
            )
            action, explanation = self._action(
                identity,
                platform_row,
                role_by_user.get(resolved_user_id or -1, ()),
                target_role,
                username_owners,
                email_owners,
                external_username_counts,
                external_email_counts,
                resolved_user_id=resolved_user_id,
                reused_managed_profile=reused_managed_profile,
            )
            entry = IdentityProvisioningEntry(
                external_identity_id=external_identity_id,
                user_id=resolved_user_id,
                username=str(identity["username"]),
                display_name=str(identity["display_name"]),
                email=(str(identity["email"]) if identity["email"] else None),
                target_role=target_role,
                action=action,
                matched_entitlements=tuple(
                    sorted({name for name, _ in matched}, key=str.casefold)
                ),
                explanation=explanation,
            )
            entries.append(entry)
            state.append(
                {
                    "entry": self._entry_payload(entry),
                    "external_active": bool(identity["is_active"]),
                    "platform": (
                        {
                            "username": str(platform_row["username"]),
                            "display_name": str(platform_row["display_name"]),
                            "email": platform_row["email"],
                            "active": bool(platform_row["is_active"]),
                            "has_local_password": bool(platform_row["password_hash"]),
                            "roles": sorted(role_by_user.get(resolved_user_id or -1, ())),
                        }
                        if platform_row is not None
                        else None
                    ),
                    "reused_managed_profile": reused_managed_profile,
                }
            )
        entries.sort(
            key=lambda item: (
                item.action.value,
                item.display_name.casefold(),
                item.username.casefold(),
            )
        )
        state.sort(key=lambda item: int(item["entry"]["external_identity_id"]))
        checksum = hashlib.sha256(
            json.dumps(
                {"provider": provider_code, "state": state},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        return IdentityProvisioningPreview(
            provider_code=provider_code,
            checksum=checksum,
            generated_at=datetime.now(UTC),
            entries=tuple(entries),
            creates=self._count(entries, IdentityProvisioningAction.CREATE),
            updates=self._count(entries, IdentityProvisioningAction.UPDATE),
            unchanged=self._count(entries, IdentityProvisioningAction.UNCHANGED),
            deactivations=self._count(
                entries,
                IdentityProvisioningAction.DEACTIVATE,
            ),
            unmapped=self._count(
                entries,
                IdentityProvisioningAction.SKIP_UNMAPPED,
            ),
            conflicts=self._count(entries, IdentityProvisioningAction.CONFLICT),
        )

    @staticmethod
    def _action(
        identity,
        platform_row,
        current_roles: tuple[str, ...],
        target_role: str | None,
        username_owners: dict[str, int],
        email_owners: dict[str, int],
        external_username_counts: dict[str, int],
        external_email_counts: dict[str, int],
        *,
        resolved_user_id: int | None,
        reused_managed_profile: bool,
    ) -> tuple[IdentityProvisioningAction, str]:
        username = str(identity["username"])
        email = str(identity["email"]) if identity["email"] else None
        linked_user_id = resolved_user_id
        display_name = str(identity["display_name"])
        if not display_name.strip() or len(display_name) > 120:
            return (
                IdentityProvisioningAction.CONFLICT,
                "The Oracle display name is not compatible with platform rules.",
            )
        if email and (
            len(email) > 254
            or "@" not in email
            or email.startswith("@")
            or email.endswith("@")
        ):
            return (
                IdentityProvisioningAction.CONFLICT,
                "The Oracle email address is not compatible with platform rules.",
            )
        if external_username_counts.get(username.casefold(), 0) > 1 or (
            email and external_email_counts.get(email.casefold(), 0) > 1
        ):
            return (
                IdentityProvisioningAction.CONFLICT,
                "Multiple Oracle identities share a platform username or email.",
            )
        if platform_row is not None and platform_row["password_hash"]:
            return (
                IdentityProvisioningAction.CONFLICT,
                "The linked account has a local password and cannot be managed "
                "automatically.",
            )
        if platform_row is not None and (
            str(platform_row["username"]).casefold() != username.casefold()
            or (
                email
                and email_owners.get(email.casefold())
                not in {None, linked_user_id}
            )
        ):
            return (
                IdentityProvisioningAction.CONFLICT,
                "The linked profile username changed or its email is owned "
                "by another platform account.",
            )
        if not bool(identity["is_active"]) or target_role is None:
            if platform_row is not None and bool(platform_row["is_active"]):
                return (
                    IdentityProvisioningAction.DEACTIVATE,
                    "The Oracle identity is inactive or no longer has a mapped "
                    "platform entitlement.",
                )
            if target_role is None and linked_user_id is None:
                return (
                    IdentityProvisioningAction.SKIP_UNMAPPED,
                    "No Oracle entitlement is mapped to a platform role.",
                )
            return (
                IdentityProvisioningAction.UNCHANGED,
                "The passwordless linked profile is already inactive.",
            )
        if platform_row is None:
            if not _USERNAME_PATTERN.fullmatch(username):
                return (
                    IdentityProvisioningAction.CONFLICT,
                    "The Oracle login is not compatible with platform username rules.",
                )
            username_owner = username_owners.get(username.casefold())
            email_owner = email_owners.get(email.casefold()) if email else None
            if username_owner is not None or email_owner is not None:
                return (
                    IdentityProvisioningAction.CONFLICT,
                    "A local platform account already owns this username or email. "
                    "Automatic linking is blocked.",
                )
            return (
                IdentityProvisioningAction.CREATE,
                "Create a passwordless profile linked to this Oracle identity.",
            )
        if reused_managed_profile:
            return (
                IdentityProvisioningAction.UPDATE,
                "Link this application's Oracle access to the existing "
                "passwordless profile for the same Oracle user and environment.",
            )
        changed = (
            str(platform_row["display_name"]) != str(identity["display_name"])
            or (str(platform_row["email"]) if platform_row["email"] else None)
            != email
            or not bool(platform_row["is_active"])
            or current_roles != (target_role,)
        )
        return (
            (
                IdentityProvisioningAction.UPDATE
                if changed
                else IdentityProvisioningAction.UNCHANGED
            ),
            (
                "Update profile, activation state, or the derived platform role."
                if changed
                else "The linked profile already matches the approved mappings."
            ),
        )

    @classmethod
    def _reusable_managed_profile(
        cls,
        identity,
        current_provider,
        username_owners: dict[str, int],
        email_owners: dict[str, int],
        platform_rows: dict[int, object],
        managed_links,
        current_provider_linked_users: set[int],
    ) -> int | None:
        """Return a proven Oracle-managed owner across application changes.

        Username or email equality alone is intentionally insufficient. Reuse is
        allowed only for a passwordless profile already linked to the same
        normalized Oracle subject on the same Oracle environment authority.
        """
        username = str(identity["username"])
        email = str(identity["email"]) if identity["email"] else None
        username_owner = username_owners.get(username.casefold())
        email_owner = email_owners.get(email.casefold()) if email else None
        if username_owner is None or email_owner not in {None, username_owner}:
            return None
        if username_owner in current_provider_linked_users:
            return None
        owner = platform_rows.get(username_owner)
        if owner is None or owner["password_hash"] is not None:
            return None
        current_authority = cls._provider_authority(current_provider)
        if current_authority is None:
            return None
        subject = str(identity["subject"]).casefold()
        for link in managed_links:
            if int(link["user_id"]) != username_owner:
                continue
            if int(link["provider_id"]) == int(current_provider["provider_id"]):
                continue
            if str(link["subject"]).casefold() != subject:
                continue
            if str(link["username"]).casefold() != username.casefold():
                continue
            if cls._provider_authority(link) == current_authority:
                return username_owner
        return None

    @staticmethod
    def _provider_authority(provider) -> tuple[str, str] | None:
        """Return the non-secret authority boundary used for safe relinking."""
        if str(provider["provider_type"]).upper() != "ORACLE_CLOUD":
            return None
        configuration = provider["safe_configuration"] or {}
        if not isinstance(configuration, dict):
            return None
        base_url = str(configuration.get("base_url") or "").strip().rstrip("/")
        if not base_url:
            return None
        issuer = str(provider["issuer_url"] or "").strip().rstrip("/")
        return base_url.casefold(), issuer.casefold()

    @staticmethod
    def _highest_role(roles) -> str | None:
        normalized = {str(role) for role in roles if str(role) in _ROLE_PRIORITY}
        return (
            max(normalized, key=lambda item: _ROLE_PRIORITY[item])
            if normalized
            else None
        )

    @staticmethod
    def _replace_role(
        connection,
        user_id: int,
        role_id: int,
        actor_user_id: int | None,
        now: datetime,
    ) -> None:
        connection.execute(
            delete(platform_user_roles).where(
                platform_user_roles.c.user_id == user_id
            )
        )
        connection.execute(
            insert(platform_user_roles).values(
                user_id=user_id,
                role_id=role_id,
                assigned_at=now,
                assigned_by_user_id=actor_user_id,
            )
        )

    @staticmethod
    def _audit(
        connection,
        event_type: str,
        entry: IdentityProvisioningEntry,
        user_id: int,
        actor_user_id: int | None,
        now: datetime,
    ) -> None:
        connection.execute(
            insert(authentication_events).values(
                event_type=event_type,
                username_snapshot=entry.username,
                actor_user_id=actor_user_id,
                success=True,
                occurred_at=now,
                details={
                    "managed_user_id": user_id,
                    "target_role": entry.target_role,
                    "external_identity_id": entry.external_identity_id,
                },
            )
        )

    @staticmethod
    def _provider(connection, provider_code: str) -> tuple[int, str]:
        code = str(provider_code).strip().casefold()
        row = connection.execute(
            select(identity_providers.c.provider_id, identity_providers.c.code).where(
                func.lower(identity_providers.c.code) == code
            )
        ).one_or_none()
        if row is None:
            raise IdentitySynchronizationError(
                "Synchronize the Oracle identity directory before provisioning "
                "platform accounts."
            )
        return int(row.provider_id), str(row.code)

    @staticmethod
    def _entry_payload(entry: IdentityProvisioningEntry) -> dict[str, object]:
        return {
            "external_identity_id": entry.external_identity_id,
            "user_id": entry.user_id,
            "username": entry.username,
            "display_name": entry.display_name,
            "email": entry.email,
            "target_role": entry.target_role,
            "action": entry.action.value,
            "matched_entitlements": list(entry.matched_entitlements),
            "explanation": entry.explanation,
        }

    @staticmethod
    def _count(
        entries: list[IdentityProvisioningEntry],
        action: IdentityProvisioningAction,
    ) -> int:
        return sum(entry.action == action for entry in entries)
