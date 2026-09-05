"""Tests for the generic platform-owned scheduling foundation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.application.automation_scheduling import (
    AutomationScheduleApplicationService,
)
from app.models.automation_schedule import (
    AutomationInputPolicy,
    AutomationScheduleFrequency,
    AutomationScheduleInput,
    AutomationScheduleOutcome,
    AutomationScheduleRunStatus,
    AutomationTargetType,
)
from app.services.automation_schedule_repository import (
    SQLAutomationScheduleRepository,
)
from app.utils.exceptions import AutomationScheduleError


def _input(
    *,
    name: str = "Monthly Forecast",
    environment_key: str = "environment-a",
    frequency: AutomationScheduleFrequency = AutomationScheduleFrequency.MONTHLY,
    first_run_local: datetime = datetime(2027, 1, 31, 9, 0),
) -> AutomationScheduleInput:
    return AutomationScheduleInput(
        environment_key=environment_key,
        name=name,
        target_type=AutomationTargetType.ORACLE_PIPELINE,
        target_key="PIPE01",
        frequency=frequency,
        timezone="Asia/Kolkata",
        first_run_local=first_run_local,
        input_policy=AutomationInputPolicy.FIXED,
        configuration={
            "variables": {
                "YEAR": "FY27",
                "STARTPERIOD": "Jan",
                "ENDPERIOD": "Mar",
            }
        },
    )


def test_monthly_recurrence_preserves_month_end_intent(tmp_path: Path) -> None:
    service = AutomationScheduleApplicationService(tmp_path / "schedule.sqlite3")

    february = service.next_occurrence(
        _input(),
        after=datetime(2027, 2, 1, tzinfo=UTC),
    )
    march = service.next_occurrence(
        _input(),
        after=datetime(2027, 2, 28, 4, 0, tzinfo=UTC),
    )

    assert february == datetime(2027, 2, 28, 3, 30, tzinfo=UTC)
    assert march == datetime(2027, 3, 31, 3, 30, tzinfo=UTC)


def test_schedule_is_scoped_to_an_oracle_environment(tmp_path: Path) -> None:
    service = AutomationScheduleApplicationService(tmp_path / "schedule.sqlite3")
    now = datetime(2026, 12, 1, tzinfo=UTC)

    first = service.create(_input(environment_key="environment-a"), now=now)
    second = service.create(_input(environment_key="environment-b"), now=now)

    assert first.name == second.name
    assert service.list_schedules(environment_key="environment-a") == (first,)
    assert service.list_schedules(environment_key="environment-b") == (second,)


def test_schedule_configuration_rejects_credentials(tmp_path: Path) -> None:
    service = AutomationScheduleApplicationService(tmp_path / "schedule.sqlite3")
    unsafe = _input()
    unsafe = AutomationScheduleInput(
        environment_key=unsafe.environment_key,
        name=unsafe.name,
        target_type=unsafe.target_type,
        target_key=unsafe.target_key,
        frequency=unsafe.frequency,
        timezone=unsafe.timezone,
        first_run_local=unsafe.first_run_local,
        input_policy=unsafe.input_policy,
        configuration={"oraclePassword": "must-not-be-stored"},
    )

    with pytest.raises(
        AutomationScheduleError,
        match="Credentials and secrets",
    ):
        service.create(unsafe, now=datetime(2026, 12, 1, tzinfo=UTC))


def test_atomic_claim_creates_only_one_occurrence(tmp_path: Path) -> None:
    database = tmp_path / "schedule.sqlite3"
    service = AutomationScheduleApplicationService(database)
    now = datetime(2027, 1, 31, 3, 30, tzinfo=UTC)
    schedule = service.create(
        _input(
            frequency=AutomationScheduleFrequency.DAILY,
            first_run_local=datetime(2027, 1, 31, 9, 0),
        ),
        now=now - timedelta(days=1),
    )
    repository_a = SQLAutomationScheduleRepository(database)
    repository_b = SQLAutomationScheduleRepository(database)

    first = repository_a.claim(
        schedule,
        next_run_at=now + timedelta(days=1),
        claimed_at=now,
        resolved_payload={"target_key": "PIPE01"},
    )
    second = repository_b.claim(
        schedule,
        next_run_at=now + timedelta(days=1),
        claimed_at=now,
        resolved_payload={"target_key": "PIPE01"},
    )

    assert first is not None
    assert second is None
    assert repository_a.list_runs(schedule.schedule_id) == (first,)


def test_claim_and_submission_preserve_execution_evidence(tmp_path: Path) -> None:
    service = AutomationScheduleApplicationService(tmp_path / "schedule.sqlite3")
    due_at = datetime(2027, 1, 31, 3, 30, tzinfo=UTC)
    schedule = service.create(
        _input(
            frequency=AutomationScheduleFrequency.ONE_TIME,
            first_run_local=datetime(2027, 1, 31, 9, 0),
        ),
        now=due_at - timedelta(hours=1),
    )

    claimed = service.claim_due(now=due_at)
    completed = service.record_submitted(
        claimed[0].run_id,
        "scheduled-execution-1",
        now=due_at + timedelta(seconds=1),
    )
    updated = service.get(schedule.schedule_id)

    assert len(claimed) == 1
    assert completed.status is AutomationScheduleRunStatus.SUBMITTED
    assert completed.execution_id == "scheduled-execution-1"
    assert updated.enabled is False
    assert updated.next_run_at is None
    assert updated.last_outcome is AutomationScheduleOutcome.SUBMITTED
    assert updated.last_execution_id == "scheduled-execution-1"

    evidence = service.list_run_evidence("environment-a")
    assert len(evidence) == 1
    assert evidence[0].schedule_name == "Monthly Forecast"
    assert evidence[0].target_key == "PIPE01"
    assert evidence[0].run.execution_id == "scheduled-execution-1"


def test_schedule_evidence_filters_by_environment_and_outcome(
    tmp_path: Path,
) -> None:
    service = AutomationScheduleApplicationService(tmp_path / "schedule.sqlite3")
    due_at = datetime(2027, 1, 31, 3, 30, tzinfo=UTC)
    for environment in ("environment-a", "environment-b"):
        service.create(
            _input(
                name=f"Forecast {environment}",
                environment_key=environment,
                frequency=AutomationScheduleFrequency.ONE_TIME,
                first_run_local=datetime(2027, 1, 31, 9, 0),
            ),
            now=due_at - timedelta(hours=1),
        )
    claimed = service.claim_due(now=due_at)
    service.record_submitted(
        claimed[0].run_id,
        "execution-a",
        now=due_at + timedelta(seconds=1),
    )
    service.record_failed(
        claimed[1].run_id,
        "Oracle validation failed.",
        now=due_at + timedelta(seconds=1),
    )

    submitted = service.list_run_evidence(
        "environment-a",
        status=AutomationScheduleRunStatus.SUBMITTED,
    )
    failed = service.list_run_evidence(
        "environment-b",
        status=AutomationScheduleRunStatus.FAILED,
    )

    assert [item.run.execution_id for item in submitted] == ["execution-a"]
    assert [item.run.error_message for item in failed] == [
        "Oracle validation failed."
    ]
