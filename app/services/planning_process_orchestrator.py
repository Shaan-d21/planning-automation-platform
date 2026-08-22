"""Catalog-driven end-to-end Planning process orchestration."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from app.models.planning_process import (
    PlanningProcessDefinition,
    PlanningProcessStepDefinition,
    PlanningProcessStepType,
)
from app.models.workflow import WorkflowRun
from app.services.workflow_engine import WorkflowEngine, WorkflowStep


ProcessStepHandler = Callable[
    [PlanningProcessStepDefinition],
    Mapping[str, Any] | None,
]


class PlanningProcessOrchestrator:
    """Translate a process definition into a durable fail-fast workflow."""

    def __init__(
        self,
        workflow_engine: WorkflowEngine,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._workflow_engine = workflow_engine
        self._logger = logger or logging.getLogger(__name__)

    def run(
        self,
        definition: PlanningProcessDefinition,
        *,
        handlers: Mapping[PlanningProcessStepType, ProcessStepHandler],
        enabled_overrides: Mapping[PlanningProcessStepType, bool] | None = None,
        execution_id: str | None = None,
    ) -> WorkflowRun:
        """Execute configured steps in order and persist every transition."""
        overrides = dict(enabled_overrides or {})
        from app.services.planning_process_preflight_service import (
            PlanningProcessPreflightService,
        )

        PlanningProcessPreflightService().validate(
            definition,
            handlers=handlers,
            enabled_overrides=overrides,
        )
        workflow_steps = tuple(
            WorkflowStep(
                name=step.name,
                action=self._action(step, handlers),
                enabled=overrides.get(
                    step.step_type,
                    step.enabled_by_default,
                ),
                skip_reason=(
                    f"Process step '{step.name}' was disabled for this run."
                ),
            )
            for step in definition.steps
        )
        self._logger.info(
            "Planning process orchestration started: code='%s', steps=%s.",
            definition.code,
            len(workflow_steps),
        )
        return self._workflow_engine.run(
            definition.code,
            workflow_steps,
            execution_id=execution_id,
        )

    @staticmethod
    def _action(
        step: PlanningProcessStepDefinition,
        handlers: Mapping[PlanningProcessStepType, ProcessStepHandler],
    ) -> Callable[[], Mapping[str, Any] | None]:
        def execute() -> Mapping[str, Any] | None:
            return handlers[step.step_type](step)

        return execute
