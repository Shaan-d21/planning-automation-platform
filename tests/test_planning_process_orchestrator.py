"""Tests for catalog-driven Planning process orchestration."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.planning_process import (
    PlanningProcessDefinition,
    PlanningProcessStepDefinition,
    PlanningProcessStepType,
)
from app.models.workflow import WorkflowStatus, WorkflowStepStatus
from app.services.planning_process_orchestrator import (
    PlanningProcessOrchestrator,
)
from app.services.workflow_engine import WorkflowEngine
from app.services.workflow_repository import SQLiteWorkflowRepository
from app.utils.exceptions import PlanningProcessError, WorkflowError


def _definition() -> PlanningProcessDefinition:
    return PlanningProcessDefinition(
        code="MONTHLY",
        display_name="Monthly Forecast",
        cycle_code="MONTHLY_FORECAST",
        steps=(
            PlanningProcessStepDefinition(
                PlanningProcessStepType.PREFLIGHT,
                "Validate",
            ),
            PlanningProcessStepDefinition(
                PlanningProcessStepType.REFRESH_CUBE,
                "Refresh",
                enabled_by_default=False,
                parameters={"jobName": "Refresh_Cube"},
            ),
            PlanningProcessStepDefinition(
                PlanningProcessStepType.RUN_PIPELINE,
                "Pipeline",
            ),
            PlanningProcessStepDefinition(
                PlanningProcessStepType.GENERATE_REPORT,
                "Report",
                parameters={"reportName": "Revenue Report"},
            ),
        ),
    )


def _orchestrator(path: Path) -> PlanningProcessOrchestrator:
    return PlanningProcessOrchestrator(
        WorkflowEngine(SQLiteWorkflowRepository(path))
    )


def test_orchestrator_runs_enabled_steps_and_records_skips(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def handler(step: PlanningProcessStepDefinition):
        calls.append(step.name)
        return {"step_type": step.step_type.value}

    handlers = {
        PlanningProcessStepType.PREFLIGHT: handler,
        PlanningProcessStepType.REFRESH_CUBE: handler,
        PlanningProcessStepType.RUN_PIPELINE: handler,
        PlanningProcessStepType.GENERATE_REPORT: handler,
    }

    run = _orchestrator(tmp_path / "history.sqlite3").run(
        _definition(),
        handlers=handlers,
    )

    assert run.status is WorkflowStatus.SUCCESS
    assert calls == ["Validate", "Pipeline", "Report"]
    assert tuple(step.status for step in run.steps) == (
        WorkflowStepStatus.SUCCESS,
        WorkflowStepStatus.SKIPPED,
        WorkflowStepStatus.SUCCESS,
        WorkflowStepStatus.SUCCESS,
    )


def test_orchestrator_can_enable_optional_refresh(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def handler(step: PlanningProcessStepDefinition):
        calls.append(step.name)
        return {}

    handlers = {
        step_type: handler for step_type in PlanningProcessStepType
    }
    _orchestrator(tmp_path / "history.sqlite3").run(
        _definition(),
        handlers=handlers,
        enabled_overrides={
            PlanningProcessStepType.REFRESH_CUBE: True,
        },
    )

    assert calls == ["Validate", "Refresh", "Pipeline", "Report"]


def test_orchestrator_fails_fast_and_persists_later_skips(
    tmp_path: Path,
) -> None:
    def handler(step: PlanningProcessStepDefinition):
        if step.step_type is PlanningProcessStepType.RUN_PIPELINE:
            raise RuntimeError("pipeline failed")
        return {}

    handlers = {
        step_type: handler for step_type in PlanningProcessStepType
    }
    repository = SQLiteWorkflowRepository(tmp_path / "history.sqlite3")
    orchestrator = PlanningProcessOrchestrator(WorkflowEngine(repository))

    with pytest.raises(WorkflowError, match="Pipeline"):
        orchestrator.run(_definition(), handlers=handlers)

    run = repository.list_recent(limit=1)[0]
    assert run.status is WorkflowStatus.FAILED
    assert run.steps[2].status is WorkflowStepStatus.FAILED
    assert run.steps[3].status is WorkflowStepStatus.SKIPPED


def test_orchestrator_requires_handler_for_enabled_step(
    tmp_path: Path,
) -> None:
    with pytest.raises(PlanningProcessError, match="No execution handler"):
        _orchestrator(tmp_path / "history.sqlite3").run(
            _definition(),
            handlers={
                PlanningProcessStepType.PREFLIGHT: lambda step: {},
            },
        )
