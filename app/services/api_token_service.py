"""Issue, verify, list, and revoke scoped external-client tokens."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database.engine import (
    DatabaseTarget,
    database_for,
    utc_datetime,
)
from app.infrastructure.database.schema import api_tokens
from app.models.api_token import (
    ApiTokenRecord,
    ApiTokenScope,
    AuthenticatedApiToken,
    IssuedApiToken,
)
from app.services.access_control_service import AccessControlService
from app.utils.exceptions import ApiTokenError


_TOKEN_MARKER = "bepm"
_PREFIX_PATTERN = re.compile(r"^[0-9a-f]{12}$")
_USAGE_WRITE_INTERVAL = timedelta(minutes=5)


class ApiTokenService:
    """Manage hashed bearer tokens without retaining recoverable secrets."""

    def __init__(
        self,
        database_target: DatabaseTarget,
        *,
        access_control: AccessControlService | None = None,
    ) -> None:
        self._database = database_for(database_target)
        self._access_control = access_control or AccessControlService(
            database_target
        )

    def create(
        self,
        *,
        user_id: int,
        name: str,
        scopes: frozenset[ApiTokenScope],
        expires_at: datetime | None,
    ) -> IssuedApiToken:
        """Create a random token and return its secret exactly once."""
        user = self._access_control.require_user(user_id)
        if not user.active:
            raise ApiTokenError("API tokens require an active platform user.")
        normalized_name = " ".join(str(name).split())
        if not normalized_name or len(normalized_name) > 120:
            raise ApiTokenError("Token name must contain 1-120 characters.")
        normalized_scopes = frozenset(ApiTokenScope(item) for item in scopes)
        if not normalized_scopes:
            raise ApiTokenError("Select at least one API-token scope.")
        normalized_expiry = self._normalize_expiry(expires_at)
        now = datetime.now(UTC)

        for _ in range(5):
            prefix = secrets.token_hex(6)
            token = f"{_TOKEN_MARKER}_{prefix}_{secrets.token_urlsafe(32)}"
            try:
                with self._database.begin() as connection:
                    token_id = int(
                        connection.execute(
                            insert(api_tokens)
                            .values(
                                user_id=user_id,
                                name=normalized_name,
                                token_prefix=prefix,
                                token_hash=self._hash(token),
                                scopes=sorted(
                                    scope.value for scope in normalized_scopes
                                ),
                                created_at=now,
                                expires_at=normalized_expiry,
                            )
                            .returning(api_tokens.c.token_id)
                        ).scalar_one()
                    )
            except IntegrityError:
                continue
            record = self.get(token_id)
            assert record is not None
            return IssuedApiToken(record=record, token=token)
        raise ApiTokenError("A unique API token could not be generated.")

    def authenticate(
        self,
        token: str,
        *,
        required_scope: ApiTokenScope,
        ip_address: str | None = None,
    ) -> AuthenticatedApiToken:
        """Resolve a valid token, scope, and currently active user."""
        prefix = self._prefix(token)
        with self._database.connect() as connection:
            row = connection.execute(
                select(api_tokens).where(
                    api_tokens.c.token_prefix == prefix
                )
            ).mappings().one_or_none()
        if row is None or not hmac.compare_digest(
            str(row["token_hash"]), self._hash(token)
        ):
            raise ApiTokenError("The API token is invalid or unavailable.")

        record = self._record(row)
        now = datetime.now(UTC)
        if record.revoked_at is not None or (
            record.expires_at is not None and record.expires_at <= now
        ):
            raise ApiTokenError("The API token is invalid or unavailable.")
        if required_scope not in record.scopes:
            raise ApiTokenError(
                "The API token does not permit this integration action."
            )
        user = self._access_control.get_user(record.user_id)
        if user is None or not user.active:
            raise ApiTokenError("The API token is invalid or unavailable.")
        self._record_usage(record, now=now, ip_address=ip_address)
        return AuthenticatedApiToken(record=record, user=user)

    def get(self, token_id: int) -> ApiTokenRecord | None:
        """Return safe metadata for one token."""
        with self._database.connect() as connection:
            row = connection.execute(
                select(api_tokens).where(api_tokens.c.token_id == token_id)
            ).mappings().one_or_none()
        return self._record(row) if row is not None else None

    def list_for_user(self, user_id: int) -> tuple[ApiTokenRecord, ...]:
        """List token metadata without ever returning secret values."""
        with self._database.connect() as connection:
            rows = connection.execute(
                select(api_tokens)
                .where(api_tokens.c.user_id == user_id)
                .order_by(api_tokens.c.created_at.desc())
            ).mappings().all()
        return tuple(self._record(row) for row in rows)

    def revoke(self, token_id: int) -> ApiTokenRecord:
        """Irreversibly revoke one retained token."""
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            changed = connection.execute(
                update(api_tokens)
                .where(
                    api_tokens.c.token_id == token_id,
                    api_tokens.c.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            ).rowcount
        record = self.get(token_id)
        if record is None:
            raise ApiTokenError(f"API token {token_id} was not found.")
        if not changed and record.revoked_at is None:
            raise ApiTokenError(f"API token {token_id} could not be revoked.")
        return record

    def _record_usage(
        self,
        record: ApiTokenRecord,
        *,
        now: datetime,
        ip_address: str | None,
    ) -> None:
        cutoff = now - _USAGE_WRITE_INTERVAL
        with self._database.begin() as connection:
            connection.execute(
                update(api_tokens)
                .where(
                    api_tokens.c.token_id == record.token_id,
                    or_(
                        api_tokens.c.last_used_at.is_(None),
                        api_tokens.c.last_used_at < cutoff,
                    ),
                )
                .values(last_used_at=now, last_used_ip=ip_address)
            )

    @staticmethod
    def _normalize_expiry(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        normalized = value if value.tzinfo is not None else value.replace(
            tzinfo=UTC
        )
        normalized = normalized.astimezone(UTC)
        if normalized <= datetime.now(UTC):
            raise ApiTokenError("Token expiry must be in the future.")
        return normalized

    @staticmethod
    def _prefix(token: str) -> str:
        parts = str(token).strip().split("_", 2)
        if (
            len(parts) != 3
            or parts[0] != _TOKEN_MARKER
            or not _PREFIX_PATTERN.fullmatch(parts[1])
            or len(parts[2]) < 32
        ):
            raise ApiTokenError("The API token is invalid or unavailable.")
        return parts[1]

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(str(token).encode("utf-8")).hexdigest()

    @staticmethod
    def _record(row) -> ApiTokenRecord:
        return ApiTokenRecord(
            token_id=int(row["token_id"]),
            user_id=int(row["user_id"]),
            name=str(row["name"]),
            token_prefix=str(row["token_prefix"]),
            scopes=frozenset(
                ApiTokenScope(str(item)) for item in (row["scopes"] or [])
            ),
            created_at=utc_datetime(row["created_at"]),
            expires_at=utc_datetime(row["expires_at"]),
            last_used_at=utc_datetime(row["last_used_at"]),
            last_used_ip=(
                str(row["last_used_ip"]) if row["last_used_ip"] else None
            ),
            revoked_at=utc_datetime(row["revoked_at"]),
        )
