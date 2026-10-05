"""Non-secret Oracle environment application configuration models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.models.environment import ApplicationInfo
from app.products.contracts import BusinessProcessType, ProductContext
from app.products.context import product_context


@dataclass(frozen=True, slots=True)
class EnvironmentConfiguration:
    """Persisted discovery and application selection for one base URL."""

    base_url: str
    deployment_mode: str
    selected_application: str | None
    selection_source: str | None
    applications: tuple[ApplicationInfo, ...]
    last_discovered_at: datetime | None
    last_discovery_error: str | None
    selected_at: datetime | None
    selected_by_user_id: int | None
    selected_business_process: BusinessProcessType = BusinessProcessType.UNKNOWN

    @property
    def is_configured(self) -> bool:
        """Return whether an application is selected for this environment."""
        return bool(self.selected_application)

    @property
    def selected_application_info(self) -> ApplicationInfo | None:
        """Return metadata for the exact selected application when verified."""

        selected = str(self.selected_application or "").casefold()
        return next(
            (item for item in self.applications if item.name.casefold() == selected),
            None,
        )

    @property
    def product_context(self) -> ProductContext | None:
        """Return verified product context without inferring from its name."""

        application = self.selected_application_info
        if application is None:
            return None
        return product_context(application)
