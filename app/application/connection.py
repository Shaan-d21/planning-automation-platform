"""Oracle EPM connection verification use case."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.clients.epm_client import EPMClient
from app.config.settings import Settings
from app.services.application_service import ApplicationService


@dataclass(frozen=True, slots=True)
class ConnectionResult:
    """Safe connection details suitable for presentation."""

    application_name: str
    environment_url: str
    deployment_mode: str | None = None
    application_type: str | None = None
    storage: str | None = None
    hybrid: bool | None = None


class VerifyConnection:
    """Authenticate with the configured Oracle EPM environment."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[Settings], EPMClient] = EPMClient,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory

    def execute(self) -> ConnectionResult:
        """Verify credentials without exposing them to the caller."""
        with self._client_factory(self._settings) as client:
            client.authenticate()
            application = ApplicationService(
                client
            ).get_configured_application()
        return ConnectionResult(
            application_name=application.name,
            environment_url=self._settings.epm_base_url,
            deployment_mode=client.deployment_mode,
            application_type=application.application_type,
            storage=application.storage,
            hybrid=application.hybrid,
        )
