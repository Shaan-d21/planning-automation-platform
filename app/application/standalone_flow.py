"""Durable sequential execution for agent-prepared standalone flows."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from app.application.operations import OperationCommandExecutor, OperationInput
from app.config.settings import Settings
from app.models.access_control import ExecutionActor
from app.models.access_control import TriggerSource
from app.models.workflow import (
    WorkflowRun,
    WorkflowStatus,
    WorkflowStepResult,
    WorkflowStepStatus,
)
from app.services.workflow_repository import SQLWorkflowRepository
from app.utils.exceptions import OperationError


@dataclass(frozen=True, slots=True)
class StandaloneFlowStepInput:
    """One fully resolved operation in a standalone flow."""

    operation_code: str
    display_name: str
    artifact_name: str
    operation_input: OperationInput
    source_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class StandaloneFlowInput:
    """One approved, ordered, stop-on-failure operation sequence."""

    name: str
    objective: str
    steps: tuple[StandaloneFlowStepInput, ...]
    recovery_source_execution_id: str | None = None
    recovery_from_sequence: int | None = None


def standalone_flow_steps(
    flow_input: StandaloneFlowInput,
) -> tuple[WorkflowStepResult, ...]:
    """Build the durable, user-visible execution plan for a flow."""
    return tuple(
        WorkflowStepResult(
            name=f"{index}. {step.display_name} - {step.artifact_name}",
            sequence=index,
            status=WorkflowStepStatus.PENDING,
            details={
                "standalone_flow_step": True,
                "operation_code": step.operation_code,
                "display_name": step.display_name,
                "artifact_name": step.artifact_name,
                "child_execution_id": f"{{execution_id}}-step-{index}",
                **(
                    {"original_sequence": step.source_sequence}
                    if step.source_sequence is not None
                    else {}
                ),
                **(
                    {
                        "recovery_source_execution_id": (
                            flow_input.recovery_source_execution_id
                        ),
                        "recovery_from_sequence": (
                            flow_input.recovery_from_sequence
                        ),
                    }
                    if flow_input.recovery_source_execution_id
                    else {}
                ),
            },
        )
        for index, step in enumerate(flow_input.steps, start=1)
    )


def bind_standalone_flow_execution_id(
    steps: tuple[WorkflowStepResult, ...],
    execution_id: str,
) -> tuple[WorkflowStepResult, ...]:
    """Resolve child execution identities after the parent ID is known."""
    return tuple(
        replace(
            step,
            details={
                **step.details,
                "child_execution_id": str(
                    step.details["child_execution_id"]
                ).format(execution_id=execution_id),
            },
        )
        for step in steps
    )


class StandaloneFlowCommandExecutor:
    """Execute resolved operations in order and retain parent evidence."""

    def __init__(
        self,
        settings: Settings,
        *,
        operation_executor_factory: Callable[..., object] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._logger = logger or logging.getLogger(__name__)
        self._operation_executor_factory = (
            operation_executor_factory or OperationCommandExecutor
        )
        self._repository = SQLWorkflowRepository(settings.database_target)

    def execute(
        self,
        flow_input: StandaloneFlowInput,
        *,
        execution_id: str,
        log_file: Path,
        actor: ExecutionActor | None = None,
        stop_requested: Callable[[], bool] | None = None,
    ) -> WorkflowRun:
        """Run every configured step, stopping immediately on failure."""
        if not flow_input.steps:
            raise OperationError("A standalone flow requires at least one step.")
        started_at = datetime.now(UTC)
        results = bind_standalone_flow_execution_id(
            standalone_flow_steps(flow_input),
            execution_id,
        )
        run = WorkflowRun(
            execution_id=execution_id,
            workflow_name=f"Standalone Flow - {flow_input.name}",
            status=WorkflowStatus.RUNNING,
            started_at=started_at,
            steps=results,
            initiated_by=actor.username if actor else None,
            initiated_by_display=actor.display_name if actor else None,
            trigger_source=(
                actor.trigger_source if actor else TriggerSource.API
            ),
            oracle_execution_username=(
                self._settings.oracle_execution_username
            ),
        )
        self._repository.save(run)
        for index, step in enumerate(flow_input.steps, start=1):
            if stop_requested is not None and stop_requested():
                return self._stop_before_step(run, results, index)
            child_id = f"{execution_id}-step-{index}"
            step_started = datetime.now(UTC)
            current = results[index - 1]
            results = _replace_flow_step(
                results,
                index - 1,
                replace(
                    current,
                    status=WorkflowStepStatus.RUNNING,
                    started_at=step_started,
                ),
            )
            run = replace(run, steps=results)
            self._repository.save(run)
            try:
                child = self._operation_executor_factory(
                    self._settings,
                    logger=self._logger.getChild(f"step_{index}"),
                ).execute(
                    step.operation_input,
                    execution_id=child_id,
                    log_file=log_file,
                    actor=actor,
                )
            except Exception as exc:
                child = self._repository.get(child_id)
                completed_at = datetime.now(UTC)
                results = _replace_flow_step(
                    results,
                    index - 1,
                    replace(
                        results[index - 1],
                        status=WorkflowStepStatus.FAILED,
                        completed_at=completed_at,
                        details=self._child_details(
                            child,
                            child_id,
                            step,
                            results[index - 1].details,
                        ),
                        error_message=" ".join(str(exc).split())[:1_500],
                    ),
                )
                results = tuple(
                    replace(
                        result,
                        status=WorkflowStepStatus.SKIPPED,
                        completed_at=completed_at,
                        details={
                            **result.details,
                            "reason": "A previous flow step failed.",
                        },
                    )
                    if position >= index
                    else result
                    for position, result in enumerate(results)
                )
                failed = replace(
                    run,
                    status=WorkflowStatus.FAILED,
                    completed_at=datetime.now(UTC),
                    steps=results,
                    error_message=(
                        f"Standalone flow stopped at step {index}: "
                        f"{' '.join(str(exc).split())[:1_200]}"
                    ),
                )
                self._repository.save(failed)
                raise OperationError(failed.error_message) from exc
            results = _replace_flow_step(
                results,
                index - 1,
                replace(
                    results[index - 1],
                    status=WorkflowStepStatus.SUCCESS,
                    completed_at=child.completed_at or datetime.now(UTC),
                    details=self._child_details(
                        child,
                        child_id,
                        step,
                        results[index - 1].details,
                    ),
                ),
            )
            run = replace(run, steps=results)
            self._repository.save(run)
        completed = replace(
            run,
            status=WorkflowStatus.SUCCESS,
            completed_at=datetime.now(UTC),
            steps=results,
        )
        self._repository.save(completed)
        return completed

    def _stop_before_step(
        self,
        run: WorkflowRun,
        results: tuple[WorkflowStepResult, ...],
        next_sequence: int,
    ) -> WorkflowRun:
        """Stop the flow without interrupting an already submitted Oracle job."""
        completed_at = datetime.now(UTC)
        stopped = tuple(
            replace(
                result,
                status=WorkflowStepStatus.SKIPPED,
                completed_at=completed_at,
                details={
                    **result.details,
                    "reason": "Stopped by the user before this step started.",
                },
            )
            if position >= next_sequence - 1
            and result.status is WorkflowStepStatus.PENDING
            else result
            for position, result in enumerate(results)
        )
        cancelled = replace(
            run,
            status=WorkflowStatus.CANCELLED,
            completed_at=completed_at,
            steps=stopped,
            error_message=(
                "The user requested a safe stop. Completed Oracle steps were "
                "retained and remaining steps were not started."
            ),
        )
        self._repository.save(cancelled)
        return cancelled

    @staticmethod
    def _child_details(
        child: WorkflowRun | None,
        child_id: str,
        step: StandaloneFlowStepInput,
        base_details: dict[str, object],
    ) -> dict[str, object]:
        details: dict[str, object] = {
            **base_details,
            "standalone_flow_step": True,
            "operation_code": step.operation_code,
            "display_name": step.display_name,
            "artifact_name": step.artifact_name,
            "child_execution_id": child_id,
            **(
                {"original_sequence": step.source_sequence}
                if step.source_sequence is not None
                else {}
            ),
        }
        if child is None:
            return details
        details["child_workflow_name"] = child.workflow_name
        details["child_status"] = child.status.value
        details["child_steps"] = [
            {
                "name": item.name,
                "sequence": item.sequence,
                "status": item.status.value,
                "details": item.details,
                "error_message": item.error_message,
            }
            for item in child.steps
        ]
        statistics = next(
            (
                item.details.get("record_statistics")
                for item in reversed(child.steps)
                if isinstance(item.details.get("record_statistics"), dict)
            ),
            None,
        )
        if statistics is not None:
            details["record_statistics"] = statistics
        oracle_evidence = next(
            (
                item.details
                for item in reversed(child.steps)
                if item.details.get("job_id") is not None
            ),
            None,
        )
        if oracle_evidence is not None:
            details["job_id"] = oracle_evidence["job_id"]
            if oracle_evidence.get("status") is not None:
                details["status"] = oracle_evidence["status"]
        return details


def _replace_flow_step(
    steps: tuple[WorkflowStepResult, ...],
    index: int,
    value: WorkflowStepResult,
) -> tuple[WorkflowStepResult, ...]:
    mutable = list(steps)
    mutable[index] = value
    return tuple(mutable)
