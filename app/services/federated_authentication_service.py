"""Resolve validated OIDC identities to governed linked platform profiles."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import NoReturn

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database.engine import DatabaseTarget, database_for
from app.infrastructure.database.schema import (
    authentication_events,
    external_identities,
    identity_providers,
    platform_users,
)
from app.models.access_control import UserAccount
from app.services.access_control_service import AccessControlService
from app.utils.exceptions import FederatedAuthenticationError


class _Denied(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class FederatedAuthenticationService:
    """Bind immutable provider subjects and authenticate managed users."""

    def __init__(self, database_target: DatabaseTarget) -> None:
        self._database = database_for(database_target)
        self._access = AccessControlService(database_target)

    def authenticate(
        self,
        provider_code: str,
        *,
        subject: str,
        username: str,
        ip_address: str | None = None,
    ) -> UserAccount:
        """Authenticate a synchronized, linked, passwordless profile."""
        provider = str(provider_code).strip().casefold()
        normalized_subject = str(subject).strip()
        normalized_username = str(username).strip()
        if not provider or not normalized_subject or not normalized_username:
            self._reject(
                normalized_username or "unknown",
                "MISSING_REQUIRED_CLAIM",
                ip_address,
            )
        if len(normalized_subject) > 255 or len(normalized_username) > 254:
            self._reject(
                normalized_username[:80] or "unknown",
                "INVALID_CLAIM_LENGTH",
                ip_address,
            )
        user_id: int
        try:
            with self._database.begin() as connection:
                provider_row = connection.execute(
                    select(identity_providers).where(
                        func.lower(identity_providers.c.code) == provider,
                        identity_providers.c.is_enabled.is_(True),
                    )
                ).mappings().one_or_none()
                if provider_row is None:
                    raise _Denied("PROVIDER_NOT_SYNCHRONIZED")
                provider_id = int(provider_row["provider_id"])
                statement = select(external_identities).where(
                    external_identities.c.provider_id == provider_id,
                    external_identities.c.authenticated_subject
                    == normalized_subject,
                )
                if connection.dialect.name == "postgresql":
                    statement = statement.with_for_update()
                identity = connection.execute(statement).mappings().one_or_none()
                first_binding = identity is None
                if first_binding:
                    candidate_statement = select(external_identities).where(
                        external_identities.c.provider_id == provider_id,
                        external_identities.c.authenticated_subject.is_(None),
                        func.lower(external_identities.c.username)
                        == normalized_username.casefold(),
                    )
                    if connection.dialect.name == "postgresql":
                        candidate_statement = candidate_statement.with_for_update()
                    candidates = connection.execute(
                        candidate_statement
                    ).mappings().all()
                    if len(candidates) != 1:
                        raise _Denied(
                            "IDENTITY_NOT_FOUND"
                            if not candidates
                            else "AMBIGUOUS_IDENTITY"
                        )
                    identity = candidates[0]
                assert identity is not None
                if (
                    str(identity["username"]).casefold()
                    != normalized_username.casefold()
                ):
                    raise _Denied("USERNAME_SUBJECT_MISMATCH")
                if not bool(identity["is_active"]):
                    raise _Denied("ORACLE_IDENTITY_INACTIVE")
                if identity["user_id"] is None:
                    raise _Denied("PLATFORM_ACCOUNT_NOT_PROVISIONED")
                user_id = int(identity["user_id"])
                user_statement = select(platform_users).where(
                    platform_users.c.user_id == user_id
                )
                if connection.dialect.name == "postgresql":
                    user_statement = user_statement.with_for_update()
                user = connection.execute(user_statement).mappings().one_or_none()
                if user is None or not bool(user["is_active"]):
                    raise _Denied("PLATFORM_ACCOUNT_INACTIVE")
                if user["password_hash"] is not None:
                    raise _Denied("LOCAL_ACCOUNT_LINK_BLOCKED")
                now = datetime.now(UTC)
                identity_values: dict[str, object] = {
                    "last_authenticated_at": now,
                    "updated_at": now,
                }
                if first_binding:
                    identity_values["authenticated_subject"] = normalized_subject
                connection.execute(
                    update(external_identities)
                    .where(
                        external_identities.c.external_identity_id
                        == int(identity["external_identity_id"])
                    )
                    .values(**identity_values)
                )
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
                connection.execute(
                    insert(authentication_events).values(
                        event_type="FEDERATED_LOGIN_SUCCESS",
                        username_snapshot=str(user["username"]),
                        actor_user_id=user_id,
                        success=True,
                        ip_address=ip_address,
                        occurred_at=now,
                        details={
                            "provider_code": str(provider_row["code"]),
                            "external_identity_id": int(
                                identity["external_identity_id"]
                            ),
                            "subject_bound": first_binding,
                        },
                    )
                )
        except _Denied as denied:
            self._reject(
                normalized_username,
                denied.reason,
                ip_address,
            )
        except IntegrityError:
            self._reject(
                normalized_username,
                "SUBJECT_BINDING_CONFLICT",
                ip_address,
            )
        account = self._access.get_user(user_id)
        if account is None or not account.active:
            self._reject(
                normalized_username,
                "PLATFORM_ACCOUNT_UNAVAILABLE",
                ip_address,
            )
        return account

    def _reject(
        self,
        username: str,
        reason: str,
        ip_address: str | None,
    ) -> NoReturn:
        with self._database.begin() as connection:
            connection.execute(
                insert(authentication_events).values(
                    event_type="FEDERATED_LOGIN_FAILED",
                    username_snapshot=(username[:80] or "unknown"),
                    actor_user_id=None,
                    success=False,
                    ip_address=ip_address,
                    occurred_at=datetime.now(UTC),
                    details={"reason": reason},
                )
            )
        raise FederatedAuthenticationError(
            "Your Oracle identity is authenticated, but no active approved "
            "platform account is available. Ask a Platform Administrator to "
            "synchronize and provision your access."
        )
