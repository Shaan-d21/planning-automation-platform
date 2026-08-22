"""Static pre-flight validation for configured Planning processes."""

from __future__ import annotations

from collections.abc import Mapping

from app.models.planning_process import (
    PlanningProcessDefinition,
    PlanningProcessStepDefinition,
    PlanningProcessStepType,
)
from app.services.planning_process_orchestrator import ProcessStepHandler
from app.utils.exceptions import PlanningProcessError


class PlanningProcessPreflightService:
    """Validate step handlers, required parameters, and safe ordering."""

    _ORDER = {
        PlanningProcessStepType.PREFLIGHT: 10,
        PlanningProcessStepType.UPDATE_VARIABLES: 20,
        PlanningProcessStepType.REFRESH_CUBE: 30,
        PlanningProcessStepType.RUN_PIPELINE: 40,
        PlanningProcessStepType.RUN_BUSINESS_RULE: 45,
        PlanningProcessStepType.RUN_DATA_MAP: 50,
        PlanningProcessStepType.VALIDATE_DATA: 60,
        PlanningProcessStepType.GENERATE_REPORT: 70,
    }

    def validate(
        self,
        definition: PlanningProcessDefinition,
        *,
        handlers: Mapping[PlanningProcessStepType, ProcessStepHandler],
        enabled_overrides: Mapping[PlanningProcessStepType, bool],
    ) -> tuple[PlanningProcessStepDefinition, ...]:
        """Return enabled steps after validating the complete definition."""
        previous_order = 0
        enabled_steps: list[PlanningProcessStepDefinition] = []
        for step in definition.steps:
            current_order = self._ORDER[step.step_type]
            if current_order < previous_order:
                raise PlanningProcessError(
                    f"Planning process '{definition.code}' step "
                    f"'{step.name}' is out of order."
                )
            previous_order = current_order
            enabled = enabled_overrides.get(
                step.step_type,
                step.enabled_by_default,
            )
            if not enabled:
                continue
            if step.step_type not in handlers:
                raise PlanningProcessError(
                    f"No execution handler is registered for enabled step "
                    f"'{step.name}'."
                )
            self._validate_parameters(step)
            enabled_steps.append(step)
        if not enabled_steps:
            raise PlanningProcessError(
                f"Planning process '{definition.code}' has no enabled steps."
            )
        return tuple(enabled_steps)

    @staticmethod
    def _validate_parameters(
        step: PlanningProcessStepDefinition,
    ) -> None:
        required_by_type = {
            PlanningProcessStepType.REFRESH_CUBE: ("jobName",),
            PlanningProcessStepType.RUN_BUSINESS_RULE: ("ruleName",),
            PlanningProcessStepType.GENERATE_REPORT: ("reportName",),
        }
        missing = [
            key
            for key in required_by_type.get(step.step_type, ())
            if not str(step.parameters.get(key, "")).strip()
        ]
        if missing:
            raise PlanningProcessError(
                f"Planning process step '{step.name}' requires parameters: "
                + ", ".join(missing)
            )
