"""Oracle application discovery and persisted selection tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.services.access_control_service import AccessControlService
from app.services.environment_configuration_service import (
    EnvironmentConfigurationService,
)
from app.utils.exceptions import APIRequestError, ConfigurationError


def _settings(database: Path, application_name: str = "") -> Settings:
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="automation.user",
        epm_password="secret",
        application_name=application_name,
        deployment_mode="cloud",
        workflow_database_file=database,
    )


class _FakeClient:
    planning_api_root = "/HyperionPlanning/rest/v3"

    def __init__(
        self,
        settings: Settings,
        applications: list[dict[str, object]],
        *,
        failure: Exception | None = None,
    ) -> None:
        self.application_name = settings.application_name
        self._applications = applications
        self._failure = failure
        self.authenticated = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def authenticate(self):
        self.authenticated = True
        if self._failure:
            raise self._failure
        return {"version": "v3"}

    def get(self, endpoint: str):
        assert self.authenticated is True
        assert endpoint.endswith("/applications")
        return {"items": self._applications}


def _factory(
    applications: list[dict[str, object]],
    *,
    failure: Exception | None = None,
):
    return lambda settings: _FakeClient(
        settings,
        applications,
        failure=failure,
    )


def test_environment_fallback_is_persisted_without_live_discovery(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path / "environment.sqlite3", "Vision")
    service = EnvironmentConfigurationService(
        settings,
        client_factory=lambda _: pytest.fail("Discovery was not expected."),
    )

    resolved = service.resolve_startup_settings()
    saved = service.require()

    assert resolved.application_name == "Vision"
    assert saved.selected_application == "Vision"
    assert saved.selection_source == "ENVIRONMENT"


def test_single_discovered_application_is_selected_automatically(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path / "environment.sqlite3")
    service = EnvironmentConfigurationService(
        settings,
        client_factory=_factory(
            [{"name": "EPBCS", "type": "HP", "adminMode": False}]
        ),
    )

    resolved = service.resolve_startup_settings()
    saved = service.require()

    assert resolved.application_name == "EPBCS"
    assert saved.selected_application == "EPBCS"
    assert saved.selection_source == "AUTO_DISCOVERY"
    assert saved.applications[0].product_type == "HP"


def test_multiple_applications_require_an_explicit_verified_selection(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path / "environment.sqlite3")
    service = EnvironmentConfigurationService(
        settings,
        client_factory=_factory(
            [{"name": "Budget"}, {"name": "Forecast"}]
        ),
    )

    resolved = service.resolve_startup_settings()
    discovered = service.require()

    assert resolved.application_name == ""
    assert discovered.selected_application is None
    assert [item.name for item in discovered.applications] == [
        "Budget",
        "Forecast",
    ]

    administrator = AccessControlService(settings.database_target).bootstrap_administrator(
        username="environment.admin",
        display_name="Environment Administrator",
        email="environment.admin@example.com",
        password="StrongPassword!123",
    )
    selected = service.select_application(
        "forecast",
        selected_by_user_id=administrator.user_id,
    )
    assert selected.selected_application == "Forecast"
    assert selected.selection_source == "ADMIN_SELECTION"
    assert selected.selected_by_user_id == administrator.user_id

    with pytest.raises(ConfigurationError, match="did not return"):
        service.select_application(
            "Unknown",
            selected_by_user_id=administrator.user_id,
        )


def test_database_selection_overrides_the_environment_fallback(
    tmp_path: Path,
) -> None:
    database = tmp_path / "environment.sqlite3"
    initial = _settings(database, "Vision")
    EnvironmentConfigurationService(initial).resolve_startup_settings()
    changed_env = replace(initial, application_name="AnotherApp")

    resolved = EnvironmentConfigurationService(
        changed_env,
        client_factory=lambda _: pytest.fail("Discovery was not expected."),
    ).resolve_startup_settings()

    assert resolved.application_name == "Vision"


def test_failed_refresh_preserves_last_successful_application_catalog(
    tmp_path: Path,
) -> None:
    database = tmp_path / "environment.sqlite3"
    settings = _settings(database)
    EnvironmentConfigurationService(
        settings,
        client_factory=_factory([{"name": "Vision"}, {"name": "Plan"}]),
    ).discover()
    failing = EnvironmentConfigurationService(
        settings,
        client_factory=_factory(
            [],
            failure=APIRequestError("Oracle is temporarily unavailable."),
        ),
    )

    with pytest.raises(APIRequestError):
        failing.discover()

    saved = failing.require()
    assert [item.name for item in saved.applications] == ["Plan", "Vision"]
    assert "temporarily unavailable" in (saved.last_discovery_error or "")
