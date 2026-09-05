"""Guardrails for the production PostgreSQL persistence architecture."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.agent import repository as agent_repository
from app.infrastructure.database.engine import database_for
from app.infrastructure.database import migration
from app.infrastructure.database.schema import metadata
from app.services import (
    access_control_service,
    api_token_service,
    business_rule_rtp_registry,
    automation_schedule_repository,
    oracle_artifact_registry,
    execution_queue_repository,
    federated_authentication_service,
    federated_provisioning_service,
    environment_configuration_service,
    identity_directory_service,
    pipeline_process_repository,
    pipeline_registry,
    planning_work_repository,
    planning_governance_repository,
    planning_validation_repository,
    process_schedule_repository,
    workflow_repository,
)
from app.utils.exceptions import ConfigurationError, DatabaseConnectionError
from sqlalchemy.exc import OperationalError


EXPECTED_TABLES = {
    "platform_users",
    "platform_roles",
    "platform_role_permissions",
    "platform_user_roles",
    "authentication_events",
    "identity_providers",
    "external_identities",
    "external_entitlements",
    "external_identity_entitlements",
    "identity_role_mappings",
    "identity_sync_runs",
    "api_tokens",
    "oracle_environment_settings",
    "oracle_artifacts",
    "business_rule_rtp_sync_runs",
    "business_rule_rtp_definitions",
    "business_rule_rtp_parameters",
    "planning_processes",
    "planning_process_versions",
    "process_run_profiles",
    "process_schedules",
    "automation_schedules",
    "automation_schedule_runs",
    "planning_cycles",
    "planning_cycle_stages",
    "planning_tasks",
    "planning_task_dependencies",
    "planning_task_executions",
    "planning_task_validations",
    "planning_approvals",
    "user_notifications",
    "workflow_runs",
    "execution_queue",
    "workflow_steps",
    "agent_conversations",
    "agent_messages",
    "agent_tool_activities",
    "agent_action_drafts",
    "agent_action_decisions",
}


def test_schema_contains_only_documented_tables() -> None:
    assert set(metadata.tables) == EXPECTED_TABLES


def test_schema_compiles_for_postgresql_identifier_limits() -> None:
    dialect = postgresql.dialect()

    for table in metadata.sorted_tables:
        CreateTable(table).compile(dialect=dialect)


def test_repositories_do_not_mutate_schema_at_runtime() -> None:
    modules = (
        access_control_service,
        api_token_service,
        business_rule_rtp_registry,
        automation_schedule_repository,
        oracle_artifact_registry,
        execution_queue_repository,
        federated_authentication_service,
        federated_provisioning_service,
        environment_configuration_service,
        identity_directory_service,
        workflow_repository,
        pipeline_process_repository,
        process_schedule_repository,
        pipeline_registry,
        planning_work_repository,
        planning_governance_repository,
        planning_validation_repository,
        agent_repository,
    )
    source = "\n".join(inspect.getsource(module) for module in modules).upper()
    assert "CREATE TABLE" not in source
    assert "ALTER TABLE" not in source
    assert "PRAGMA TABLE_INFO" not in source


def test_alembic_has_one_production_head() -> None:
    project_root = Path(__file__).resolve().parents[1]
    scripts = ScriptDirectory.from_config(
        Config(str(project_root / "alembic.ini"))
    )
    assert scripts.get_heads() == ["0020_execution_identity"]
    assert all(len(revision.revision) <= 32 for revision in scripts.walk_revisions())


def test_runtime_rejects_sqlite_url() -> None:
    with pytest.raises(ConfigurationError, match="SQLite URLs"):
        database_for("sqlite:///runtime.sqlite3")


def test_schema_check_translates_database_authentication_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnavailableDatabase:
        def connect(self):
            raise OperationalError(
                "connect",
                {},
                Exception("password authentication failed: secret-value"),
            )

    monkeypatch.setattr(
        migration,
        "database_for",
        lambda _: UnavailableDatabase(),
    )

    with pytest.raises(DatabaseConnectionError) as captured:
        migration.assert_schema_current(
            "postgresql+psycopg://user:secret-value@localhost/database",
            project_root=Path(__file__).resolve().parents[1],
        )

    message = str(captured.value)
    assert "Could not connect to the PostgreSQL platform database" in message
    assert "secret-value" not in message
