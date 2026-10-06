"""Application registration, membership, and session isolation tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.agent.repository import SQLAgentRepository
from app.config.settings import Settings
from app.services.access_control_service import AccessControlService
from app.services.application_workspace_service import ApplicationWorkspaceService
from app.services.environment_configuration_service import (
    EnvironmentConfigurationService,
)
from app.services.session_security_service import ClientContext, SessionSecurityService
from app.products.runtime_context import RuntimeApplicationContextResolver
from app.utils.exceptions import AgentConversationError, ConfigurationError


def _settings(database: Path) -> Settings:
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="automation.user",
        epm_password="secret",
        application_name="",
        deployment_mode="cloud",
        workflow_database_file=database,
    )


class _DiscoveryClient:
    planning_api_root = "/HyperionPlanning/rest/v3"

    def __init__(self, settings: Settings) -> None:
        self.application_name = settings.application_name

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def authenticate(self):
        return {"version": "v3"}

    def get(self, endpoint: str):
        assert endpoint.endswith("/applications")
        return {
            "items": [
                {"name": "Plan", "type": "HP", "appType": "PBCS"},
                {"name": "Close", "type": "HP", "appType": "FCCS"},
            ]
        }


def _client() -> ClientContext:
    return ClientContext(
        ip_address="192.0.2.1",
        country_code="IN",
        user_agent="pytest",
        cloudflare_ray=None,
    )


def test_registered_applications_are_isolated_by_membership_and_session(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path / "workspaces.sqlite3")
    access = AccessControlService(settings.database_target)
    administrator = access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Secure passphrase 123!",
    )
    planner = access.create_user(
        username="planner",
        display_name="Planner",
        email=None,
        password="Secure planner passphrase 123!",
        roles=("USER",),
        actor_user_id=administrator.user_id,
    )
    configuration_service = EnvironmentConfigurationService(
        settings,
        client_factory=_DiscoveryClient,
    )
    configuration_service.discover()
    configuration = configuration_service.select_application(
        "Plan",
        selected_by_user_id=administrator.user_id,
    )
    workspaces = ApplicationWorkspaceService(settings.database_target)
    registered = workspaces.synchronize(
        configuration,
        actor_user_id=administrator.user_id,
    )

    assert [item.application_name for item in registered] == ["Close", "Plan"]
    assert [item.business_process.value for item in registered] == [
        "FCCS",
        "PLANNING",
    ]

    sessions = SessionSecurityService(settings.database_target)
    sessions.start(
        "admin-session",
        user_id=administrator.user_id,
        username=administrator.username,
        authentication_method="local",
        client=_client(),
    )
    sessions.start(
        "planner-session",
        user_id=planner.user_id,
        username=planner.username,
        authentication_method="local",
        client=_client(),
    )
    admin_default = workspaces.establish_default_for_session(
        "admin-session",
        user_id=administrator.user_id,
        configuration=configuration,
    )
    planner_default = workspaces.establish_default_for_session(
        "planner-session",
        user_id=planner.user_id,
        configuration=configuration,
    )

    assert admin_default is not None
    assert admin_default.application_name == "Plan"
    assert planner_default is not None
    assert planner_default.application_name == "Plan"
    assert [
        item.application_name
        for item in workspaces.available_for_user(planner.user_id)
    ] == ["Plan"]

    close = next(item for item in registered if item.application_name == "Close")
    selected = workspaces.select_for_session(
        "admin-session",
        user_id=administrator.user_id,
        application_id=close.application_id,
    )
    assert selected.application_name == "Close"
    assert selected.business_process.value == "FCCS"
    runtime_context = RuntimeApplicationContextResolver(
        settings,
        workspaces=workspaces,
        environment_configuration=configuration_service,
    ).resolve(
        "admin-session",
        user_id=administrator.user_id,
    )
    assert runtime_context.application_id == close.application_id
    assert runtime_context.settings.application_name == "Close"
    assert runtime_context.business_process.value == "FCCS"
    assert runtime_context.operations == ()
    assert "fccs-journals" in {
        item.code for item in runtime_context.navigation
    }
    planner_current = workspaces.current_for_session(
        "planner-session",
        user_id=planner.user_id,
    )
    assert planner_current is not None
    assert planner_current.application_name == "Plan"

    with pytest.raises(ConfigurationError, match="not assigned"):
        workspaces.select_for_session(
            "planner-session",
            user_id=planner.user_id,
            application_id=close.application_id,
        )

    conversations = SQLAgentRepository(settings.database_target)
    planning_conversation = conversations.create_conversation(
        user_id=administrator.user_id,
        application_id=admin_default.application_id,
        provider="gemini",
        model="test-model",
    )
    fccs_conversation = conversations.create_conversation(
        user_id=administrator.user_id,
        application_id=close.application_id,
        provider="gemini",
        model="test-model",
    )

    assert [
        item.conversation_id
        for item in conversations.list_conversations(
            administrator.user_id,
            application_id=admin_default.application_id,
        )
    ] == [planning_conversation.conversation_id]
    assert [
        item.conversation_id
        for item in conversations.list_conversations(
            administrator.user_id,
            application_id=close.application_id,
        )
    ] == [fccs_conversation.conversation_id]
    with pytest.raises(AgentConversationError, match="not found"):
        conversations.require_conversation_application(
            planning_conversation.conversation_id,
            administrator.user_id,
            close.application_id,
        )


def test_runtime_context_rebinds_a_session_when_the_environment_changes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "environment-change.sqlite3"
    fccs_settings = _settings(database)
    access = AccessControlService(fccs_settings.database_target)
    administrator = access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Secure passphrase 123!",
    )
    sessions = SessionSecurityService(fccs_settings.database_target)
    sessions.start(
        "admin-session",
        user_id=administrator.user_id,
        username=administrator.username,
        authentication_method="local",
        client=_client(),
    )
    fccs_configuration = EnvironmentConfigurationService(
        fccs_settings,
        client_factory=_DiscoveryClient,
    )
    fccs_configuration.discover()
    selected_fccs = fccs_configuration.select_application(
        "Close",
        selected_by_user_id=administrator.user_id,
    )
    workspaces = ApplicationWorkspaceService(fccs_settings.database_target)
    fccs_workspace = workspaces.establish_default_for_session(
        "admin-session",
        user_id=administrator.user_id,
        configuration=selected_fccs,
    )
    assert fccs_workspace is not None
    assert fccs_workspace.business_process.value == "FCCS"

    planning_settings = replace(
        fccs_settings,
        epm_base_url="https://planning.example.oraclecloud.com",
        application_name="Vision",
    )
    planning_configuration = EnvironmentConfigurationService(
        planning_settings,
        client_factory=_DiscoveryClient,
    )
    resolved_settings = planning_configuration.resolve_startup_settings()
    runtime = RuntimeApplicationContextResolver(
        resolved_settings,
        workspaces=workspaces,
        environment_configuration=planning_configuration,
    ).resolve(
        "admin-session",
        user_id=administrator.user_id,
    )

    assert runtime.application_name == "Vision"
    assert runtime.business_process.value == "PLANNING"
    assert runtime.workspace.application_id != fccs_workspace.application_id
