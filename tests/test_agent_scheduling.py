"""Tests for governed agent scheduling over the shared scheduler."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from app.agent.capabilities import AgentCapabilityGateway
from app.agent.graph import AgentGraphOrchestrator
from app.agent.models import AgentToolCall
from app.agent.service import AgentApplicationService
from app.application.operations import (
    PipelineOperationPreview,
    PipelineStagePreview,
)
from app.config.settings import Settings
from app.models.automation_schedule import (
    AutomationInputPolicy,
    AutomationScheduleFrequency,
    AutomationScheduleOutcome,
    AutomationTargetType,
)
from app.models.access_control import Permission, RoleCode, UserAccount


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="service-user",
        epm_password="secret",
        application_name="Vision",
        workflow_database_file=tmp_path / "agent-schedule.sqlite3",
    )


def _gateway(tmp_path: Path):
    settings = _settings(tmp_path)
    catalog = Mock()
    catalog.discover_registered.return_value = SimpleNamespace(
        pipelines=(SimpleNamespace(code="PIPE01", name="Monthly Forecast"),),
        data_integrations=(),
    )
    catalog.preflight_pipeline.return_value = PipelineOperationPreview(
        code="PIPE01",
        display_name="Monthly Forecast",
        variables=(),
        file_requirements=(),
        stages=(
            PipelineStagePreview(
                name="LOAD",
                display_name="Load data",
                job_count=1,
                runs_in_parallel=False,
            ),
        ),
    )
    schedule_service = Mock()
    coordinator = Mock()
    coordinator.preview.return_value = SimpleNamespace(
        next_run_at=datetime(2027, 1, 1, 6, tzinfo=UTC),
        next_run_local=datetime(2027, 1, 1, 6),
    )
    gateway = AgentCapabilityGateway(
        settings,
        control_center=Mock(),
        data_review=Mock(),
        operation_catalog=catalog,
        schedule_service=schedule_service,
        schedule_coordinator=coordinator,
    )
    return gateway, schedule_service, coordinator


def test_agent_schedule_create_is_live_validated(tmp_path: Path) -> None:
    gateway, _, coordinator = _gateway(tmp_path)

    result = gateway.execute(
        AgentToolCall(
            name="prepare_schedule_action",
            arguments={
                "action": "CREATE",
                "objective": "Run the forecast every month.",
                "artifact_name": "PIPE01",
                "input_values": {
                    "name": "Monthly forecast",
                    "frequency": "MONTHLY",
                    "timezone": "UTC",
                    "first_run_local": "2027-01-01T06:00",
                    "input_policy": "ORACLE_DEFAULTS",
                    "variables": {},
                    "inbox_files": {},
                    "misfire_policy": "RUN_ONCE",
                    "enabled": True,
                },
            },
        )
    )

    draft = result["action_draft"]
    assert draft["target_code"] == "pipeline-schedule-create"
    assert draft["input_values"]["next_run_at"] == "2027-01-01T06:00:00+00:00"
    schedule_input = coordinator.preview.call_args.args[0]
    assert schedule_input.target_key == "PIPE01"
    assert schedule_input.frequency is AutomationScheduleFrequency.MONTHLY
    assert schedule_input.input_policy is AutomationInputPolicy.ORACLE_DEFAULTS

    # LangGraph replays the canonical reviewed inputs while it constructs the
    # approval. Server-computed preview fields must be accepted but recalculated.
    replay = gateway.execute(
        AgentToolCall(
            name="prepare_schedule_action",
            arguments={
                "action": "CREATE",
                "objective": "Run the forecast every month.",
                "artifact_name": "PIPE01",
                "input_values": draft["input_values"],
            },
        )
    )
    assert replay["action_draft"]["input_values"] == draft["input_values"]
    assert coordinator.preview.call_count == 2


def test_agent_pause_lists_only_active_pipeline_schedules(tmp_path: Path) -> None:
    gateway, schedule_service, _ = _gateway(tmp_path)
    schedule_service.list_schedules.return_value = (
        SimpleNamespace(
            schedule_id=7,
            name="Monthly forecast",
            target_type=AutomationTargetType.ORACLE_PIPELINE,
            target_key="PIPE01",
            enabled=True,
            next_run_at=datetime(2027, 1, 1, tzinfo=UTC),
            last_outcome=AutomationScheduleOutcome.NEVER,
        ),
        SimpleNamespace(
            schedule_id=8,
            name="Paused forecast",
            target_type=AutomationTargetType.ORACLE_PIPELINE,
            target_key="PIPE01",
            enabled=False,
            next_run_at=None,
            last_outcome=AutomationScheduleOutcome.NEVER,
        ),
    )

    choices = gateway.artifact_catalog("pipeline-schedule-pause")

    assert choices == (
        ("schedule:7", "Monthly forecast · PIPE01 · Active"),
    )


def test_explicit_schedule_request_is_routed_without_model_choice() -> None:
    call = AgentGraphOrchestrator._deterministic_schedule_call(
        {
            "allowed_tool_names": ["prepare_schedule_action"],
            "messages": [
                {
                    "role": "user",
                    "content": "Schedule the Monthly Forecast Pipeline every week.",
                }
            ],
        }
    )

    assert call is not None
    assert call.name == "prepare_schedule_action"
    assert call.arguments["action"] == "CREATE"
    assert (
        AgentGraphOrchestrator._schedule_frequency_prefill(
            "Schedule the Monthly Forecast Pipeline every week."
        )
        == "WEEKLY"
    )


def test_schedule_permission_does_not_expose_operation_execution_tools() -> None:
    now = datetime.now(UTC)
    user = UserAccount(
        user_id=11,
        username="scheduler",
        display_name="Scheduler",
        email=None,
        active=True,
        roles=(RoleCode.SERVICE_ADMINISTRATOR,),
        permissions=frozenset(
            {Permission.AGENT_USE, Permission.SCHEDULE_MANAGE}
        ),
        created_at=now,
        updated_at=now,
    )

    allowed = AgentApplicationService._allowed_tool_names(user)

    assert "prepare_schedule_action" in allowed
    assert "prepare_operation_action" not in allowed
