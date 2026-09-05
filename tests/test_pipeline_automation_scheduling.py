"""Tests for live Pipeline schedule validation and dispatch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.application.automation_schedule_targets import (
    AutomationScheduleCoordinator,
    PipelineScheduleTargetAdapter,
    RTPRegistrySyncScheduleTargetAdapter,
)
from app.application.automation_scheduling import (
    AutomationScheduleApplicationService,
)
from app.application.operations import (
    PipelineFilePreview,
    PipelineOperationPreview,
    PipelineStagePreview,
    PipelineVariablePreview,
)
from app.config.settings import Settings
from app.models.access_control import TriggerSource
from app.models.automation_schedule import (
    AutomationInputPolicy,
    AutomationScheduleFrequency,
    AutomationScheduleInput,
    AutomationScheduleOutcome,
    AutomationTargetType,
)
from app.models.oracle_artifact import OracleEnvironment
from app.utils.exceptions import AutomationScheduleError, OperationError


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="administrator",
        epm_password="secret",
        application_name="Vision",
        workflow_database_file=tmp_path / "schedule.sqlite3",
        execution_runtime="worker",
    )


def _preview() -> PipelineOperationPreview:
    return PipelineOperationPreview(
        code="PIPE01",
        display_name="Monthly Forecast",
        variables=(
            PipelineVariablePreview(
                name="YEAR",
                display_name="Planning Year",
                default_value=None,
                required=True,
                editable=True,
            ),
            PipelineVariablePreview(
                name="IMPORTMODE",
                display_name="Import Mode",
                default_value="Replace",
                required=False,
                editable=True,
            ),
        ),
        file_requirements=(
            PipelineFilePreview(
                key="DATA_FILE",
                display_name="Forecast data file",
                configured_reference=None,
                required=True,
                allowed_extensions=(".csv",),
                consumers=("Load / Forecast Integration",),
            ),
        ),
        stages=(
            PipelineStagePreview(
                name="LOAD",
                display_name="Load data",
                job_count=1,
                runs_in_parallel=False,
            ),
        ),
    )


def _input(
    settings: Settings,
    *,
    input_policy: AutomationInputPolicy = AutomationInputPolicy.FIXED,
    configuration=None,
) -> AutomationScheduleInput:
    environment = OracleEnvironment.from_settings(
        settings.epm_base_url,
        settings.application_name,
    )
    return AutomationScheduleInput(
        environment_key=environment.key,
        name="Monthly Forecast",
        target_type=AutomationTargetType.ORACLE_PIPELINE,
        target_key="PIPE01",
        frequency=AutomationScheduleFrequency.MONTHLY,
        timezone="UTC",
        first_run_local=datetime(2027, 1, 1, 6, 0),
        input_policy=input_policy,
        configuration=(
            configuration
            if configuration is not None
            else {
                "variables": {"YEAR": "FY27"},
                "inbox_files": {
                    "DATA_FILE": "#epminbox/forecast.csv"
                },
            }
        ),
    )


def _coordinator(tmp_path: Path, notification_service=None):
    settings = _settings(tmp_path)
    catalog = Mock()
    catalog.preflight_pipeline.return_value = _preview()
    manager = Mock()
    manager.submit.return_value = SimpleNamespace(execution_id="execution-1")
    schedules = AutomationScheduleApplicationService(settings.database_target)
    adapter = PipelineScheduleTargetAdapter(settings, catalog=catalog)
    coordinator = AutomationScheduleCoordinator(
        schedules,
        manager,
        (adapter,),
        notification_service=notification_service,
        environment_url=settings.epm_base_url,
        application_name=settings.application_name,
    )
    return settings, catalog, manager, schedules, coordinator


def test_pipeline_schedule_is_live_validated_before_persistence(
    tmp_path: Path,
) -> None:
    settings, catalog, _, _, coordinator = _coordinator(tmp_path)

    schedule = coordinator.create(
        _input(settings),
        now=datetime(2026, 12, 1, tzinfo=UTC),
    )

    assert schedule.target_key == "PIPE01"
    assert schedule.configuration["variables"] == {"YEAR": "FY27"}
    catalog.preflight_pipeline.assert_called_once_with("PIPE01")


def test_pipeline_schedule_requires_live_variables_and_files(
    tmp_path: Path,
) -> None:
    settings, _, _, _, coordinator = _coordinator(tmp_path)

    with pytest.raises(
        AutomationScheduleError,
        match="requires values for: Planning Year",
    ):
        coordinator.preview(
            _input(
                settings,
                configuration={
                    "inbox_files": {
                        "DATA_FILE": "#epminbox/forecast.csv"
                    }
                },
            ),
            now=datetime(2026, 12, 1, tzinfo=UTC),
        )

    with pytest.raises(
        AutomationScheduleError,
        match="requires Oracle Inbox files",
    ):
        coordinator.preview(
            _input(
                settings,
                configuration={"variables": {"YEAR": "FY27"}},
            ),
            now=datetime(2026, 12, 1, tzinfo=UTC),
        )


def test_pipeline_schedule_rejects_local_files_and_dynamic_policy(
    tmp_path: Path,
) -> None:
    settings, _, _, _, coordinator = _coordinator(tmp_path)

    with pytest.raises(AutomationScheduleError, match="not a local computer"):
        coordinator.preview(
            _input(
                settings,
                configuration={
                    "variables": {"YEAR": "FY27"},
                    "inbox_files": {"DATA_FILE": "C:\\files\\forecast.csv"},
                },
            ),
            now=datetime(2026, 12, 1, tzinfo=UTC),
        )

    with pytest.raises(AutomationScheduleError, match="not enabled yet"):
        coordinator.preview(
            _input(
                settings,
                input_policy=AutomationInputPolicy.DYNAMIC,
            ),
            now=datetime(2026, 12, 1, tzinfo=UTC),
        )


def test_due_pipeline_is_queued_with_scheduled_audit_actor(
    tmp_path: Path,
) -> None:
    settings, catalog, manager, schedules, coordinator = _coordinator(tmp_path)
    due_at = datetime(2027, 1, 1, 6, 0, tzinfo=UTC)
    schedule = coordinator.create(
        _input(settings),
        now=due_at - timedelta(days=1),
    )
    catalog.reset_mock()

    results = coordinator.dispatch_due(now=due_at)

    assert len(results) == 1
    assert results[0].status == "SUBMITTED"
    assert results[0].execution_id == "execution-1"
    submitted_input = manager.submit.call_args.args[0]
    submitted_actor = manager.submit.call_args.kwargs["actor"]
    assert submitted_input.pipeline_code == "PIPE01"
    assert submitted_input.variables == {"YEAR": "FY27"}
    assert submitted_input.inbox_files == {
        "DATA_FILE": "#epminbox/forecast.csv"
    }
    assert submitted_actor.trigger_source is TriggerSource.SCHEDULED
    assert catalog.preflight_pipeline.call_count == 1
    updated = schedules.get(schedule.schedule_id)
    assert updated.last_outcome is AutomationScheduleOutcome.SUBMITTED
    assert updated.last_execution_id == "execution-1"


def test_active_pipeline_execution_skips_scheduled_occurrence(
    tmp_path: Path,
) -> None:
    settings, _, manager, schedules, coordinator = _coordinator(tmp_path)
    due_at = datetime(2027, 1, 1, 6, 0, tzinfo=UTC)
    schedule = coordinator.create(
        _input(settings),
        now=due_at - timedelta(days=1),
    )
    manager.submit.side_effect = OperationError(
        "Pipeline 'PIPE01' already has an active execution."
    )

    results = coordinator.dispatch_due(now=due_at)

    assert results[0].status == "SKIPPED"
    assert schedules.get(schedule.schedule_id).last_outcome is (
        AutomationScheduleOutcome.SKIPPED
    )


def test_skipped_handoff_sends_non_terminal_scheduler_notification(
    tmp_path: Path,
) -> None:
    notifications = Mock()
    settings, _, manager, _, coordinator = _coordinator(
        tmp_path,
        notification_service=notifications,
    )
    due_at = datetime(2027, 1, 1, 6, 0, tzinfo=UTC)
    coordinator.create(_input(settings), now=due_at - timedelta(days=1))
    manager.submit.side_effect = OperationError(
        "Pipeline 'PIPE01' already has an active execution."
    )

    coordinator.dispatch_due(now=due_at)

    event = notifications.publish.call_args.args[0]
    assert event.status.value == "SKIPPED"
    assert event.job_or_integration_name == "PIPE01"
    assert "active execution" in str(event.error_message)


def test_due_rtp_registry_sync_completes_without_operation_queue(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    environment = OracleEnvironment.from_settings(
        settings.epm_base_url,
        settings.application_name,
    )
    schedules = AutomationScheduleApplicationService(settings.database_target)
    manager = Mock()
    exporter = Mock()
    exporter.normalize_snapshot_prefix.return_value = "BISP_CalcManager_RTP"
    exporter.generate.return_value = SimpleNamespace(
        snapshot_name="BISP_CalcManager_RTP_20270101",
        content=b"snapshot",
    )
    registry = Mock()
    registry.import_package.return_value = SimpleNamespace(
        sync_run_id=91,
        rules_imported=4,
        prompts_imported=7,
    )
    adapter = RTPRegistrySyncScheduleTargetAdapter(
        settings,
        registry=registry,
        exporter=exporter,
    )
    coordinator = AutomationScheduleCoordinator(
        schedules,
        manager,
        (adapter,),
    )
    due_at = datetime(2027, 1, 1, 6, 0, tzinfo=UTC)
    schedule = coordinator.create(
        AutomationScheduleInput(
            environment_key=environment.key,
            name="Weekly Business Rule prompt sync",
            target_type=AutomationTargetType.RTP_REGISTRY_SYNC,
            target_key="BISP_CalcManager_RTP",
            frequency=AutomationScheduleFrequency.WEEKLY,
            timezone="UTC",
            first_run_local=datetime(2027, 1, 1, 6, 0),
            input_policy=AutomationInputPolicy.FIXED,
        ),
        now=due_at - timedelta(days=1),
    )

    results = coordinator.dispatch_due(now=due_at)

    assert results[0].status == "COMPLETED"
    assert "4 rule(s)" in results[0].message
    manager.submit.assert_not_called()
    exporter.ensure_supported.assert_called()
    exporter.generate.assert_called_once_with("BISP_CalcManager_RTP")
    registry.import_package.assert_called_once_with(
        "BISP_CalcManager_RTP_20270101.zip",
        b"snapshot",
    )
    exporter.delete.assert_called_once_with("BISP_CalcManager_RTP_20270101")
    updated = schedules.get(schedule.schedule_id)
    assert updated.last_outcome is AutomationScheduleOutcome.COMPLETED
    assert updated.last_execution_id is None
