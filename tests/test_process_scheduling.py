"""Tests for durable Planning Process scheduling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.application.scheduling import ProcessScheduleApplicationService
from app.config.settings import Settings
from app.models.planning_process import (
    PlanningProcessDefinition,
    ProcessContextMode,
)
from app.models.process_schedule import (
    ProcessScheduleInput,
    ScheduleContextMode,
    ScheduleFrequency,
    ScheduleRunOutcome,
)
from app.services.process_schedule_repository import (
    SQLiteProcessScheduleRepository,
)
from app.utils.exceptions import ScheduleError


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="administrator",
        epm_password="secret",
        application_name="Vision",
        workflow_database_file=tmp_path / "history.sqlite3",
    )


def _definition(
    *,
    context_mode: ProcessContextMode = ProcessContextMode.PIPELINE_DEFAULTS,
) -> PlanningProcessDefinition:
    return PlanningProcessDefinition(
        code="FORECAST",
        display_name="Monthly Forecast",
        cycle_code="FORECAST_CYCLE",
        steps=(),
        context_mode=context_mode,
    )


def _service(tmp_path: Path):
    process_service = Mock()
    process_service.get_definition.return_value = _definition()
    process_service.preflight.return_value = SimpleNamespace(
        file_requirements=()
    )
    designer = Mock()
    execution_manager = Mock()
    execution_manager.submit.return_value = SimpleNamespace(
        execution_id="scheduled123"
    )
    service = ProcessScheduleApplicationService(
        _settings(tmp_path),
        process_service=process_service,
        process_designer=designer,
        execution_manager=execution_manager,
    )
    return service, process_service, designer, execution_manager


def test_monthly_recurrence_keeps_anchor_day_across_short_month(
    tmp_path: Path,
) -> None:
    service, *_ = _service(tmp_path)
    schedule_input = ProcessScheduleInput(
        name="Month End Forecast",
        process_code="FORECAST",
        frequency=ScheduleFrequency.MONTHLY,
        timezone="Asia/Calcutta",
        first_run_local=datetime(2027, 1, 31, 9, 0),
        context_mode=ScheduleContextMode.PIPELINE_DEFAULTS,
    )

    february = service.next_occurrence(
        schedule_input,
        after=datetime(2027, 2, 1, tzinfo=UTC),
    )
    march = service.next_occurrence(
        schedule_input,
        after=datetime(2027, 2, 28, 4, 0, tzinfo=UTC),
    )

    assert february == datetime(2027, 2, 28, 3, 30, tzinfo=UTC)
    assert march == datetime(2027, 3, 31, 3, 30, tzinfo=UTC)


def test_create_and_run_now_validate_before_submission(tmp_path: Path) -> None:
    service, process_service, _, execution_manager = _service(tmp_path)
    schedule = service.create(
        ProcessScheduleInput(
            name="Daily Forecast",
            process_code="forecast",
            frequency=ScheduleFrequency.DAILY,
            timezone="UTC",
            first_run_local=(
                datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
            ),
            context_mode=ScheduleContextMode.PIPELINE_DEFAULTS,
        )
    )

    result = service.run_now(schedule.schedule_id)

    assert schedule.process_code == "FORECAST"
    assert schedule.next_run_at is not None
    assert result.outcome is ScheduleRunOutcome.SUBMITTED
    assert result.execution_id == "scheduled123"
    assert process_service.preflight.call_count == 2
    submitted = execution_manager.submit.call_args.args[0]
    assert submitted.process_code == "FORECAST"


def test_preview_validates_context_and_returns_local_and_utc_occurrence(
    tmp_path: Path,
) -> None:
    service, process_service, _, _ = _service(tmp_path)
    schedule_input = ProcessScheduleInput(
        name="India Morning Forecast",
        process_code="FORECAST",
        frequency=ScheduleFrequency.DAILY,
        timezone="Asia/Kolkata",
        first_run_local=datetime(2027, 1, 2, 9, 0),
        context_mode=ScheduleContextMode.PIPELINE_DEFAULTS,
    )

    preview = service.preview(
        schedule_input,
        now=datetime(2027, 1, 1, 0, 0, tzinfo=UTC),
    )

    assert preview.next_run_at == datetime(2027, 1, 2, 3, 30, tzinfo=UTC)
    assert preview.next_run_local.hour == 9
    assert str(preview.next_run_local.tzinfo) == "Asia/Kolkata"
    process_service.preflight.assert_called_once()


def test_schedule_rejects_preset_that_needs_local_upload(
    tmp_path: Path,
) -> None:
    service, process_service, designer, _ = _service(tmp_path)
    process_service.get_definition.return_value = _definition(
        context_mode=ProcessContextMode.PROMPT_EACH_RUN
    )
    designer.get_profile.return_value = SimpleNamespace(
        profile_id=7,
        name="Forecast with upload",
        required_upload_keys=("INPUT_FILE",),
    )

    with pytest.raises(ScheduleError, match="requires a local upload"):
        service.create(
            ProcessScheduleInput(
                name="Unsafe Forecast",
                process_code="FORECAST",
                frequency=ScheduleFrequency.WEEKLY,
                timezone="UTC",
                first_run_local=(
                    datetime.now(UTC).replace(tzinfo=None)
                    + timedelta(hours=1)
                ),
                context_mode=ScheduleContextMode.RUN_PRESET,
                preset_id=7,
            )
        )


def test_atomic_claim_prevents_duplicate_scheduler_handoff(
    tmp_path: Path,
) -> None:
    path = tmp_path / "history.sqlite3"
    first = SQLiteProcessScheduleRepository(path)
    second = SQLiteProcessScheduleRepository(path)
    now = datetime.now(UTC)
    schedule = first.create(
        ProcessScheduleInput(
            name="Claim Test",
            process_code="FORECAST",
            frequency=ScheduleFrequency.DAILY,
            timezone="UTC",
            first_run_local=now.replace(tzinfo=None),
            context_mode=ScheduleContextMode.PIPELINE_DEFAULTS,
        ),
        next_run_at=now,
        now=now,
    )

    next_run = now + timedelta(days=1)

    assert first.claim(
        schedule,
        next_run_at=next_run,
        triggered_at=now,
    ) is True
    assert second.claim(
        schedule,
        next_run_at=next_run,
        triggered_at=now,
    ) is False
