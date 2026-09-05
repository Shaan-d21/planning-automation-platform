"""Discover and persist the selected Oracle Planning application."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select, update

from app.clients.epm_client import EPMClient
from app.config.settings import Settings
from app.infrastructure.database.engine import DatabaseTarget, database_for
from app.infrastructure.database.schema import oracle_environment_settings
from app.models.environment import ApplicationInfo
from app.models.environment_configuration import EnvironmentConfiguration
from app.services.application_service import ApplicationService
from app.utils.exceptions import ConfigurationError


class EnvironmentConfigurationService:
    """Own application discovery without persisting Oracle credentials."""

    def __init__(
        self,
        settings: Settings,
        *,
        database_target: DatabaseTarget | None = None,
        client_factory: Callable[[Settings], EPMClient] = EPMClient,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._database = database_for(
            database_target or settings.database_target
        )
        self._client_factory = client_factory
        self._logger = logger or logging.getLogger(__name__)

    def resolve_startup_settings(self) -> Settings:
        """Resolve the active application using DB, env, then live discovery."""
        configuration = self.get()
        if configuration and configuration.selected_application:
            return replace(
                self._settings,
                application_name=configuration.selected_application,
            )

        configured_fallback = self._settings.application_name.strip()
        if configured_fallback:
            self._persist_selection(
                configured_fallback,
                source="ENVIRONMENT",
                selected_by_user_id=None,
            )
            return self._settings

        try:
            discovered = self.discover()
        except Exception as exc:
            self._logger.warning(
                "Oracle application auto-discovery did not complete: %s",
                exc,
            )
            return self._settings

        if len(discovered.applications) == 1:
            application = discovered.applications[0].name
            selected = self._persist_selection(
                application,
                source="AUTO_DISCOVERY",
                selected_by_user_id=None,
            )
            self._logger.info(
                "Automatically selected Oracle Planning application '%s'.",
                application,
            )
            return replace(
                self._settings,
                application_name=selected.selected_application or "",
            )
        if len(discovered.applications) > 1:
            self._logger.warning(
                "Oracle returned %s applications; a Service Administrator "
                "must select one in Environment Setup.",
                len(discovered.applications),
            )
        return self._settings

    def get(self) -> EnvironmentConfiguration | None:
        """Return persisted configuration for the current Oracle base URL."""
        with self._database.connect() as connection:
            row = connection.execute(
                select(oracle_environment_settings).where(
                    oracle_environment_settings.c.base_url
                    == self._settings.epm_base_url
                )
            ).mappings().one_or_none()
        return self._configuration(row) if row is not None else None

    def discover(self) -> EnvironmentConfiguration:
        """Retrieve all visible Oracle applications and retain safe metadata."""
        now = datetime.now(UTC)
        try:
            with self._client_factory(self._settings) as client:
                client.authenticate()
                applications = ApplicationService(client).get_applications()
        except Exception as exc:
            self._persist_discovery((), now=now, error=str(exc))
            raise
        configuration = self._persist_discovery(
            applications,
            now=now,
            error=None,
        )
        self._logger.info(
            "Discovered %s Oracle Planning application(s).",
            len(applications),
        )
        return configuration

    def select_application(
        self,
        application_name: str,
        *,
        selected_by_user_id: int,
    ) -> EnvironmentConfiguration:
        """Select one application previously verified by live discovery."""
        requested = str(application_name).strip()
        if not requested:
            raise ConfigurationError("Select an Oracle Planning application.")
        configuration = self.get()
        if configuration is None or not configuration.applications:
            configuration = self.discover()
        matches = {
            item.name.casefold(): item.name for item in configuration.applications
        }
        verified_name = matches.get(requested.casefold())
        if verified_name is None:
            raise ConfigurationError(
                f"Oracle did not return an application named '{requested}'. "
                "Refresh application discovery and select a current value."
            )
        return self._persist_selection(
            verified_name,
            source="ADMIN_SELECTION",
            selected_by_user_id=selected_by_user_id,
        )

    def _persist_discovery(
        self,
        applications: tuple[ApplicationInfo, ...],
        *,
        now: datetime,
        error: str | None,
    ) -> EnvironmentConfiguration:
        existing = self.get()
        payload = (
            [asdict(item) for item in existing.applications]
            if error and existing and existing.applications
            else [asdict(item) for item in applications]
        )
        values: dict[str, Any] = {
            "base_url": self._settings.epm_base_url,
            "deployment_mode": self._settings.resolved_deployment_mode,
            "discovered_applications": payload,
            "last_discovered_at": now,
            "last_discovery_error": error,
            "updated_at": now,
        }
        with self._database.begin() as connection:
            if existing is None:
                connection.execute(
                    insert(oracle_environment_settings).values(
                        **values,
                        created_at=now,
                    )
                )
            else:
                connection.execute(
                    update(oracle_environment_settings)
                    .where(
                        oracle_environment_settings.c.base_url
                        == self._settings.epm_base_url
                    )
                    .values(**values)
                )
        return self.require()

    def _persist_selection(
        self,
        application_name: str,
        *,
        source: str,
        selected_by_user_id: int | None,
    ) -> EnvironmentConfiguration:
        now = datetime.now(UTC)
        existing = self.get()
        values: dict[str, Any] = {
            "base_url": self._settings.epm_base_url,
            "deployment_mode": self._settings.resolved_deployment_mode,
            "selected_application": application_name,
            "selection_source": source,
            "selected_at": now,
            "selected_by_user_id": selected_by_user_id,
            "updated_at": now,
        }
        with self._database.begin() as connection:
            if existing is None:
                connection.execute(
                    insert(oracle_environment_settings).values(
                        **values,
                        discovered_applications=[],
                        created_at=now,
                    )
                )
            else:
                connection.execute(
                    update(oracle_environment_settings)
                    .where(
                        oracle_environment_settings.c.base_url
                        == self._settings.epm_base_url
                    )
                    .values(**values)
                )
        return self.require()

    def require(self) -> EnvironmentConfiguration:
        """Return the current row after an expected persistence operation."""
        configuration = self.get()
        if configuration is None:
            raise ConfigurationError(
                "Oracle environment configuration could not be persisted."
            )
        return configuration

    @staticmethod
    def _configuration(row: Mapping[str, Any]) -> EnvironmentConfiguration:
        raw_applications = row.get("discovered_applications") or []
        applications = tuple(
            ApplicationInfo(
                name=str(item.get("name") or "").strip(),
                product_type=(
                    str(item["product_type"])
                    if item.get("product_type") is not None
                    else None
                ),
                application_type=(
                    str(item["application_type"])
                    if item.get("application_type") is not None
                    else None
                ),
                storage=(
                    str(item["storage"])
                    if item.get("storage") is not None
                    else None
                ),
                admin_mode=item.get("admin_mode"),
                hybrid=item.get("hybrid"),
                unicode=item.get("unicode"),
                theme=(
                    str(item["theme"])
                    if item.get("theme") is not None
                    else None
                ),
            )
            for item in raw_applications
            if isinstance(item, Mapping) and str(item.get("name") or "").strip()
        )
        return EnvironmentConfiguration(
            base_url=str(row["base_url"]),
            deployment_mode=str(row["deployment_mode"]),
            selected_application=(
                str(row["selected_application"])
                if row.get("selected_application")
                else None
            ),
            selection_source=(
                str(row["selection_source"])
                if row.get("selection_source")
                else None
            ),
            applications=applications,
            last_discovered_at=row.get("last_discovered_at"),
            last_discovery_error=(
                str(row["last_discovery_error"])
                if row.get("last_discovery_error")
                else None
            ),
            selected_at=row.get("selected_at"),
            selected_by_user_id=(
                int(row["selected_by_user_id"])
                if row.get("selected_by_user_id") is not None
                else None
            ),
        )
