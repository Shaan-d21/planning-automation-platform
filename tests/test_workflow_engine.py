"""Tests for reusable workflow execution and durable run history."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.workflow import WorkflowStatus, WorkflowStepStatus
from app.models.access_control import ExecutionActor, TriggerSource
from app.services.workflow_engine import WorkflowEngine, WorkflowStep
from app.services.workflow_repository import SQLiteWorkflowRepository
from app.models.job import JobDiagnostics, JobResult
from app.utils.exceptions import JobFailedError, WorkflowError


def _engine(path: Path) -> tuple[WorkflowEngine, SQLiteWorkflowRepository]:
    repository = SQLiteWorkflowRepository(path)
    return WorkflowEngine(repository), repository


def test_workflow_executes_in_order_and_persists_skipped_step(
    tmp_path: Path,
) -> None:
    engine, repository = _engine(tmp_path / "history.sqlite3")
    calls: list[str] = []

    run = engine.run(
        "MONTHLY_FORECAST",
        (
            WorkflowStep(
                "Pipeline",
                lambda: calls.append("pipeline") or {"job_id": 101},
            ),
            WorkflowStep(
                "Data Map",
                lambda: calls.append("map") or {},
                enabled=False,
                skip_reason="Publishing disabled.",
            ),
        ),
    )

    stored = repository.get(run.execution_id)
    assert calls == ["pipeline"]
    assert run.status is WorkflowStatus.SUCCESS
    assert stored is not None
    assert stored.steps[1].status is WorkflowStepStatus.SKIPPED
    assert stored.steps[1].details["reason"] == "Publishing disabled."


def test_workflow_stops_after_failure_and_records_blocked_steps(
    tmp_path: Path,
) -> None:
    engine, repository = _engine(tmp_path / "history.sqlite3")
    calls: list[str] = []

    with pytest.raises(WorkflowError, match="Pipeline"):
        engine.run(
            "MONTHLY_FORECAST",
            (
                WorkflowStep(
                    "Pipeline",
                    lambda: (_ for _ in ()).throw(
                        RuntimeError("Oracle job failed")
                    ),
                ),
                WorkflowStep(
                    "Data Map",
                    lambda: calls.append("map") or {},
                ),
            ),
        )

    assert calls == []
    stored = repository.list_recent(limit=1)[0]
    assert stored is not None
    assert stored.status is WorkflowStatus.FAILED
    assert stored.steps[0].status is WorkflowStepStatus.FAILED
    assert stored.steps[1].status is WorkflowStepStatus.SKIPPED


def test_repository_lists_recent_runs_in_reverse_order(
    tmp_path: Path,
) -> None:
    engine, repository = _engine(tmp_path / "history.sqlite3")
    first = engine.run("FIRST", (WorkflowStep("One", lambda: {}),))
    second = engine.run("SECOND", (WorkflowStep("Two", lambda: {}),))

    recent = repository.list_recent(limit=2)

    assert {run.execution_id for run in recent} == {
        first.execution_id,
        second.execution_id,
    }
    assert len(recent) == 2


def test_workflow_accepts_caller_supplied_execution_id(
    tmp_path: Path,
) -> None:
    engine, repository = _engine(tmp_path / "history.sqlite3")

    run = engine.run(
        "MONTHLY_FORECAST",
        (WorkflowStep("Preflight", lambda: {"ready": True}),),
        execution_id="web-execution-123",
    )

    assert run.execution_id == "web-execution-123"
    assert repository.get("web-execution-123") == run


def test_workflow_persists_actor_and_trigger_source(tmp_path: Path) -> None:
    engine, repository = _engine(tmp_path / "history.sqlite3")

    run = engine.run(
        "MONTHLY_FORECAST",
        (WorkflowStep("Pipeline", lambda: {}),),
        actor=ExecutionActor(
            username="planner",
            display_name="Forecast Planner",
            trigger_source=TriggerSource.MANUAL,
        ),
        oracle_execution_username="epm.integration",
    )

    stored = repository.get(run.execution_id)
    assert stored is not None
    assert stored.initiated_by == "planner"
    assert stored.initiated_by_display == "Forecast Planner"
    assert stored.trigger_source is TriggerSource.MANUAL
    assert stored.oracle_execution_username == "epm.integration"


def test_workflow_persists_failed_oracle_load_counts(tmp_path: Path) -> None:
    engine, repository = _engine(tmp_path / "history.sqlite3")
    job = JobResult(job_id=91, status=1, descriptive_status="Error")
    error = JobFailedError(
        job,
        diagnostics=JobDiagnostics(
            job=job,
            details={
                "items": [
                    {
                        "recordsRead": 25,
                        "recordsProcessed": 24,
                        "recordsRejected": 1,
                    }
                ]
            },
        ),
    )

    with pytest.raises(WorkflowError):
        engine.run(
            "IMPORT_DATA",
            (WorkflowStep("Import data", lambda: (_ for _ in ()).throw(error)),),
        )

    failed_step = repository.list_recent(limit=1)[0].steps[0]
    statistics = failed_step.details["record_statistics"]
    assert statistics["records_read"] == 25
    assert statistics["records_processed"] == 24
    assert statistics["records_rejected"] == 1
