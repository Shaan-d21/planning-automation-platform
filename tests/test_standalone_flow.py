"""Tests for governed, durable standalone Planning flows."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.application.execution_payloads import (
    standalone_flow_from_payload,
    standalone_flow_payload,
)
from app.application.operation_execution_manager import (
    OperationExecutionManager,
    OperationExecutionStatus,
)
from app.application.operations import (
    BusinessRuleOperationInput,
    DataMapOperationInput,
    OperationKind,
)
from app.application.standalone_flow import (
    StandaloneFlowCommandExecutor,
    StandaloneFlowInput,
    StandaloneFlowStepInput,
)
from app.config.settings import Settings
from app.models.access_control import ExecutionActor, TriggerSource
from app.models.workflow import (
    WorkflowRun,
    WorkflowStatus,
    WorkflowStepResult,
    WorkflowStepStatus,
)
from app.services.workflow_repository import SQLWorkflowRepository
from app.utils.exceptions import OperationError
from app.web.application import _standalone_flow_progress


def _settings(tmp_path: Path, *, runtime: str = "web") -> Settings:
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="administrator",
        epm_password="secret",
        application_name="Vision",
        workflow_database_file=tmp_path / "standalone-flow.sqlite3",
        execution_runtime=runtime,
    )


def _flow() -> StandaloneFlowInput:
    return StandaloneFlowInput(
        name="Calculate and publish",
        objective="Calculate the forecast and publish it to reporting.",
        steps=(
            StandaloneFlowStepInput(
                operation_code="business-rules",
                display_name="Business Rules",
                artifact_name="Calculate Forecast",
                operation_input=BusinessRuleOperationInput(
                    rule_name="Calculate Forecast",
                    runtime_prompts={"Year": "FY27"},
                ),
            ),
            StandaloneFlowStepInput(
                operation_code="data-maps",
                display_name="Data Maps",
                artifact_name="Forecast to Reporting",
                operation_input=DataMapOperationInput(
                    data_map_name="Forecast to Reporting",
                    clear_target=False,
                    member_overrides={},
                    exclusion_overrides={},
                ),
            ),
        ),
    )


def test_standalone_flow_payload_round_trips() -> None:
    actor = ExecutionActor(
        username="planner",
        display_name="Planning User",
        trigger_source=TriggerSource.AI_AGENT,
    )

    decoded, decoded_actor = standalone_flow_from_payload(
        standalone_flow_payload(_flow(), actor)
    )

    assert decoded == _flow()
    assert decoded_actor == actor


def test_manager_persists_standalone_flow_before_worker_execution(
    tmp_path: Path,
) -> None:
    manager = OperationExecutionManager(_settings(tmp_path))

    execution = manager.submit_flow(_flow())

    assert execution.operation_kind is OperationKind.STANDALONE_FLOW
    assert execution.status is OperationExecutionStatus.QUEUED
    retained = manager.get_workflow(execution.execution_id)
    assert retained is not None
    assert retained.status is WorkflowStatus.QUEUED
    assert [step.status.value for step in retained.steps] == [
        "PENDING",
        "PENDING",
    ]
    assert retained.steps[0].details == {
        "standalone_flow_step": True,
        "operation_code": "business-rules",
        "display_name": "Business Rules",
        "artifact_name": "Calculate Forecast",
        "child_execution_id": f"{execution.execution_id}-step-1",
    }
    manager.shutdown()


def test_standalone_flow_stops_after_first_failed_operation(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    class Executor:
        def execute(self, operation_input, *, execution_id, log_file, actor=None):
            name = (
                operation_input.rule_name
                if isinstance(operation_input, BusinessRuleOperationInput)
                else operation_input.data_map_name
            )
            calls.append(name)
            if isinstance(operation_input, DataMapOperationInput):
                raise RuntimeError("Oracle Data Map failed")
            return WorkflowRun(
                execution_id=execution_id,
                workflow_name=name,
                status=WorkflowStatus.SUCCESS,
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )

    executor = StandaloneFlowCommandExecutor(
        _settings(tmp_path),
        operation_executor_factory=lambda *_args, **_kwargs: Executor(),
    )
    flow = _flow()
    flow_with_later_step = StandaloneFlowInput(
        name=flow.name,
        objective=flow.objective,
        steps=(
            *flow.steps,
            StandaloneFlowStepInput(
                operation_code="business-rules",
                display_name="Business Rules",
                artifact_name="Should Not Run",
                operation_input=BusinessRuleOperationInput(
                    rule_name="Should Not Run",
                    runtime_prompts={},
                ),
            ),
        ),
    )

    with pytest.raises(OperationError, match="stopped at step 2"):
        executor.execute(
            flow_with_later_step,
            execution_id="flow-failure",
            log_file=tmp_path / "flow.log",
        )

    assert calls == ["Calculate Forecast", "Forecast to Reporting"]
    retained = SQLWorkflowRepository(
        _settings(tmp_path).database_target
    ).get("flow-failure")
    assert retained is not None
    assert retained.status is WorkflowStatus.FAILED
    assert [step.status.value for step in retained.steps] == [
        "SUCCESS",
        "FAILED",
        "SKIPPED",
    ]
    assert retained.steps[1].details["operation_code"] == "data-maps"
    assert retained.steps[2].details["artifact_name"] == "Should Not Run"
    assert retained.steps[2].details["reason"] == (
        "A previous flow step failed."
    )


def test_live_flow_projection_includes_child_oracle_evidence() -> None:
    now = datetime.now(UTC)
    parent = WorkflowRun(
        execution_id="parent-flow",
        workflow_name="Standalone Flow - Monthly Forecast",
        status=WorkflowStatus.RUNNING,
        started_at=now,
        steps=(
            WorkflowStepResult(
                name="1. Business Rules - Calculate Forecast",
                sequence=1,
                status=WorkflowStepStatus.RUNNING,
                started_at=now,
                details={
                    "standalone_flow_step": True,
                    "operation_code": "business-rules",
                    "display_name": "Business Rules",
                    "artifact_name": "Calculate Forecast",
                    "child_execution_id": "parent-flow-step-1",
                },
            ),
            WorkflowStepResult(
                name="2. Data Maps - Forecast to Reporting",
                sequence=2,
                status=WorkflowStepStatus.PENDING,
                details={
                    "standalone_flow_step": True,
                    "operation_code": "data-maps",
                    "display_name": "Data Maps",
                    "artifact_name": "Forecast to Reporting",
                    "child_execution_id": "parent-flow-step-2",
                },
            ),
        ),
    )
    child = WorkflowRun(
        execution_id="parent-flow-step-1",
        workflow_name="Business Rules - Calculate Forecast",
        status=WorkflowStatus.RUNNING,
        started_at=now,
        steps=(
            WorkflowStepResult(
                name="Execute Business Rules",
                sequence=3,
                status=WorkflowStepStatus.RUNNING,
                started_at=now,
                details={
                    "job_id": 418,
                    "status": "Submitted",
                    "target_name": "Calculate Forecast",
                },
            ),
        ),
    )

    class Manager:
        def get_workflow(self, execution_id: str):
            return child if execution_id == child.execution_id else None

    serialized_steps = [
        {
            "name": step.name,
            "sequence": step.sequence,
            "status": step.status.value,
            "started_at": step.started_at.isoformat()
            if step.started_at
            else None,
            "completed_at": None,
            "details": step.details,
            "error_message": step.error_message,
        }
        for step in parent.steps
    ]
    progress = _standalone_flow_progress(
        Manager(),
        parent,
        serialized_steps,
    )

    assert progress is not None
    assert progress["current_step"]["sequence"] == 1
    assert progress["current_step"]["oracle_job_id"] == 418
    assert progress["current_step"]["active_stage"] == (
        "Execute Business Rules"
    )
    assert progress["steps"][1]["status"] == "PENDING"
