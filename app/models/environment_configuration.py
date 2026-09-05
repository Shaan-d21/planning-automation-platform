"""Non-secret Oracle environment application configuration models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.models.environment import ApplicationInfo


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

    @property
    def is_configured(self) -> bool:
        """Return whether an application is selected for this environment."""
        return bool(self.selected_application)
