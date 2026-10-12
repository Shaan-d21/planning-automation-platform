"""Register Oracle environments using deployment-owned service credentials."""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.clients.epm_client import EPMClient
from app.config.settings import Settings
from app.models.application_workspace import ApplicationWorkspace
from app.services.application_workspace_service import (
    ApplicationWorkspaceService,
)
from app.services.environment_configuration_service import (
    EnvironmentConfigurationService,
)
from app.utils.exceptions import ConfigurationError


class OracleEnvironmentRegistryService:
    """Discover and register applications for an administrator-supplied URL."""

    def __init__(
        self,
        settings: Settings,
        *,
        workspaces: ApplicationWorkspaceService,
        client_factory: Callable[[Settings], EPMClient] = EPMClient,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._workspaces = workspaces
        self._client_factory = client_factory
        self._logger = logger or logging.getLogger(__name__)

    def register(
        self,
        base_url: str,
        *,
        actor_user_id: int,
    ) -> tuple[ApplicationWorkspace, ...]:
        """Verify one endpoint and grant its discovered apps to the actor."""

        scoped_settings = self._settings.for_oracle_application(
            base_url=base_url,
            application_name="",
        )
        configuration = EnvironmentConfigurationService(
            scoped_settings,
            client_factory=self._client_factory,
            logger=self._logger.getChild("discovery"),
        ).discover()
        if not configuration.applications:
            raise ConfigurationError(
                "Oracle authentication succeeded, but this account cannot "
                "see any EPM applications at that environment."
            )
        registered = self._workspaces.synchronize(
            configuration,
            actor_user_id=actor_user_id,
        )
        self._logger.info(
            "Registered %s Oracle application(s) for a verified environment.",
            len(registered),
        )
        return tuple(item for item in registered if item.active)

