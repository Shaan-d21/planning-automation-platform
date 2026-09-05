"""Sequential, fail-fast workflow orchestration with durable history."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.models.workflow import (
    WorkflowRun,
    WorkflowStatus,
    WorkflowStepResult,
    WorkflowStepStatus,
)
from app.models.access_control import ExecutionActor, TriggerSource
from app.application.execution_evidence import workflow_failure_details
from app.services.workflow_repository import WorkflowRepository
from app.utils.exceptions import WorkflowError


StepAction = Callable[[], Mapping[str, Any] | None]


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    """Executable workflow step and its run-time enablement decision."""

    name: str
    action: StepAction
    enabled: bool = True
    skip_reason: str = "Disabled by workflow options."


class WorkflowEngine:
    """Execute workflow steps in order and stop safely at the first failure."""

    def __init__(
        self,
        repository: WorkflowRepository,
        *,
        logger: logging.Logger | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._logger = logger or logging.getLogger(__name__)
        self._now = now or (lambda: datetime.now(UTC))

    def run(
        self,
        workflow_name: str,
        steps: Sequence[WorkflowStep],
        *,
        execution_id: str | None = None,
        actor: ExecutionActor | None = None,
        oracle_execution_username: str | None = None,
    ) -> WorkflowRun:
        """Execute steps sequentially and return their durable run record."""
        name = str(workflow_name).strip()
        if not name:
            raise WorkflowError("Workflow name cannot be empty.")
        if not steps:
            raise WorkflowError("Workflow requires at least one step.")

        resolved_execution_id = str(execution_id or uuid4()).strip()
        if not resolved_execution_id:
            raise WorkflowError("Workflow execution ID cannot be empty.")
        results = tuple(
            WorkflowStepResult(
                name=step.name,
                sequence=index,
                status=WorkflowStepStatus.PENDING,
            )
            for index, step in enumerate(steps, start=1)
        )
        run = WorkflowRun(
            execution_id=resolved_execution_id,
            workflow_name=name,
            status=WorkflowStatus.RUNNING,
            started_at=self._now(),
            steps=results,
            initiated_by=(actor.username if actor else None),
            initiated_by_display=(actor.display_name if actor else None),
            trigger_source=(actor.trigger_source if actor else TriggerSource.API),
            oracle_execution_username=oracle_execution_username,
        )
        self._repository.save(run)
        self._logger.info(
            "Workflow started: name='%s', execution_id='%s'.",
            name,
            resolved_execution_id,
        )

        for index, step in enumerate(steps):
            if not step.enabled:
                results = _replace_step(
                    results,
                    index,
                    replace(
                        results[index],
                        status=WorkflowStepStatus.SKIPPED,
                        completed_at=self._now(),
                        details={"reason": step.skip_reason},
                    ),
                )
                run = replace(run, steps=results)
                self._repository.save(run)
                continue

            started_at = self._now()
            results = _replace_step(
                results,
                index,
                replace(
                    results[index],
                    status=WorkflowStepStatus.RUNNING,
                    started_at=started_at,
                ),
            )
            run = replace(run, steps=results)
            self._repository.save(run)
            try:
                details = dict(step.action() or {})
            except Exception as exc:
                completed_at = self._now()
                message = " ".join(str(exc).split())[:1_500]
                results = _replace_step(
                    results,
                    index,
                    replace(
                        results[index],
                        status=WorkflowStepStatus.FAILED,
                        completed_at=completed_at,
                        details=workflow_failure_details(exc),
                        error_message=message,
                    ),
                )
                results = tuple(
                    replace(
                        result,
                        status=WorkflowStepStatus.SKIPPED,
                        completed_at=completed_at,
                        details={
                            "reason": (
                                f"Blocked by failed step '{step.name}'."
                            )
                        },
                    )
                    if later_index > index
                    else result
                    for later_index, result in enumerate(results)
                )
                run = replace(
                    run,
                    status=WorkflowStatus.FAILED,
                    completed_at=completed_at,
                    steps=results,
                    error_message=message,
                )
                self._repository.save(run)
                self._logger.error(
                    "Workflow failed: name='%s', execution_id='%s', "
                    "step='%s', error=%s.",
                    name,
                    execution_id,
                    step.name,
                    exc,
                )
                raise WorkflowError(
                    f"Workflow '{name}' failed at step '{step.name}': {exc}"
                ) from exc

            results = _replace_step(
                results,
                index,
                replace(
                    results[index],
                    status=WorkflowStepStatus.SUCCESS,
                    completed_at=self._now(),
                    details=details,
                ),
            )
            run = replace(run, steps=results)
            self._repository.save(run)

        run = replace(
            run,
            status=WorkflowStatus.SUCCESS,
            completed_at=self._now(),
            steps=results,
        )
        self._repository.save(run)
        self._logger.info(
            "Workflow completed: name='%s', execution_id='%s'.",
            name,
            resolved_execution_id,
        )
        return run


def _replace_step(
    results: tuple[WorkflowStepResult, ...],
    index: int,
    result: WorkflowStepResult,
) -> tuple[WorkflowStepResult, ...]:
    mutable = list(results)
    mutable[index] = result
    return tuple(mutable)
