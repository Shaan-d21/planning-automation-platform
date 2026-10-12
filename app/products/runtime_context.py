"""Request-scoped Oracle application and product composition."""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.config.settings import Settings
from app.models.application_workspace import ApplicationWorkspace
from app.products.contracts import (
    BusinessProcessType,
    CapabilityDefinition,
    EPMProductProvider,
    NavigationDefinition,
    OperationDefinition,
)
from app.products.registry import PRODUCT_PROVIDER_REGISTRY
from app.services.application_workspace_service import ApplicationWorkspaceService
from app.services.environment_configuration_service import (
    EnvironmentConfigurationService,
)
from app.utils.exceptions import ConfigurationError


@dataclass(frozen=True, slots=True)
class RuntimeApplicationContext:
    """Authorized application identity and its provider-composed behavior."""

    workspace: ApplicationWorkspace
    settings: Settings
    provider: EPMProductProvider | None
    navigation: tuple[NavigationDefinition, ...]
    capabilities: tuple[CapabilityDefinition, ...]
    operations: tuple[OperationDefinition, ...]

    @property
    def application_id(self) -> int:
        return self.workspace.application_id

    @property
    def application_name(self) -> str:
        return self.workspace.application_name

    @property
    def business_process(self) -> BusinessProcessType:
        return self.workspace.business_process


class RuntimeApplicationContextResolver:
    """Resolve product behavior from durable, authorized session state."""

    def __init__(
        self,
        settings: Settings,
        *,
        workspaces: ApplicationWorkspaceService,
        environment_configuration: EnvironmentConfigurationService,
        deployment_application_id: int | None = None,
    ) -> None:
        self._settings = settings
        self._workspaces = workspaces
        self._environment_configuration = environment_configuration
        self._deployment_application_id = deployment_application_id

    def resolve(
        self,
        raw_session_id: str,
        *,
        user_id: int,
    ) -> RuntimeApplicationContext:
        """Return only a workspace assigned to both user and session."""

        workspace = self._workspaces.current_for_session(
            raw_session_id,
            user_id=user_id,
        )
        if workspace is None:
            workspace = self._workspaces.establish_default_for_session(
                raw_session_id,
                user_id=user_id,
                configuration=self._environment_configuration.get(),
            )
        if workspace is None:
            raise ConfigurationError(
                "No Oracle application workspace is assigned to this session."
            )
        business_process = workspace.business_process
        provider = PRODUCT_PROVIDER_REGISTRY.get(business_process)
        settings = self._settings.for_oracle_application(
            base_url=workspace.environment_base_url,
            application_name=workspace.application_name,
        )
        if (
            self._deployment_application_id is not None
            and workspace.application_id != self._deployment_application_id
        ):
            # A secondary environment owns its own cloud/on-premises mode;
            # never inherit the deployment environment's fixed classification.
            settings = replace(settings, deployment_mode="auto")
        navigation = PRODUCT_PROVIDER_REGISTRY.navigation_for(business_process)
        return RuntimeApplicationContext(
            workspace=workspace,
            settings=settings,
            provider=provider,
            navigation=navigation,
            capabilities=PRODUCT_PROVIDER_REGISTRY.capabilities_for(
                business_process
            ),
            operations=PRODUCT_PROVIDER_REGISTRY.operations_for(
                business_process
            ),
        )
