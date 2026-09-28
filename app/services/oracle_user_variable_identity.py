"""Resolve the Oracle identity used for Planning user-variable assignments."""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.config.settings import Settings
from app.infrastructure.database.engine import DatabaseTarget, database_for
from app.infrastructure.database.schema import (
    external_identities,
    identity_providers,
)
from app.models.access_control import UserAccount
from app.models.oracle_artifact import OracleEnvironment


class OracleUserVariableIdentityResolver:
    """Map a platform user to one environment-scoped Oracle username.

    Oracle-linked profiles use the username retained by the verified identity
    synchronization. Local-only platform profiles deliberately fall back to
    the configured Oracle integration account; their local username is never
    sent to Planning as though it were an Oracle identity.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        database_target: DatabaseTarget | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._database = database_for(
            database_target or settings.database_target
        )
        self._service_username = settings.oracle_execution_username.strip()
        self._environment_key = OracleEnvironment.from_settings(
            settings.epm_base_url,
            settings.application_name,
        ).key
        self._logger = logger or logging.getLogger(__name__)

    def resolve(self, user: UserAccount) -> str:
        """Return the linked Oracle username or the service-account fallback."""
        statement = (
            select(external_identities.c.username)
            .select_from(
                external_identities.join(
                    identity_providers,
                    identity_providers.c.provider_id
                    == external_identities.c.provider_id,
                )
            )
            .where(
                external_identities.c.user_id == user.user_id,
                external_identities.c.is_active.is_(True),
                identity_providers.c.is_enabled.is_(True),
                identity_providers.c.provider_type == "ORACLE_CLOUD",
                identity_providers.c.environment_key == self._environment_key,
            )
            .order_by(external_identities.c.external_identity_id)
        )
        with self._database.connect() as connection:
            usernames = tuple(
                str(value).strip()
                for value in connection.execute(statement).scalars().all()
                if str(value).strip()
            )
        distinct = {
            username.casefold(): username for username in usernames
        }
        if len(distinct) == 1:
            return next(iter(distinct.values()))
        if len(distinct) > 1:
            self._logger.warning(
                "Multiple Oracle identities are linked to platform user %s "
                "for the active environment; using the service account.",
                user.user_id,
            )
        return self._service_username
