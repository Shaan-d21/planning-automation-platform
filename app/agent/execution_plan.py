"""Validated, checkpoint-safe plans for understood business tasks.

These plans record what the agent may prepare. They never contain Oracle
credentials, execute operations, or bypass live artifact selection, guided
inputs, human approval, and worker monitoring.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

from app.application.operations import OPERATION_DEFINITIONS


@dataclass(frozen=True, slots=True)
class AgentExecutionPlanStep:
    sequence: int
    operation_code: str
    display_name: str
    category: str
    risk_level: str
    route: str
    approval_required: bool


@dataclass(frozen=True, slots=True)
class AgentExecutionPlan:
    task_intent: str
    objective: str
    status: str
    steps: tuple[AgentExecutionPlanStep, ...]
    approval_required: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_intent": self.task_intent,
            "objective": self.objective,
            "status": self.status,
            "steps": [asdict(item) for item in self.steps],
            "approval_required": self.approval_required,
        }


class AgentExecutionPlanBuilder:
    """Build a plan only from registered platform operation definitions."""

    _DEFINITIONS = {item.code: item for item in OPERATION_DEFINITIONS}

    @classmethod
    def build(
        cls,
        task_context: dict[str, Any] | None,
        operation_codes: Sequence[str],
    ) -> AgentExecutionPlan:
        context = task_context if isinstance(task_context, dict) else {}
        intent = str(context.get("intent") or "UNKNOWN").strip().upper()
        objective = str(context.get("objective") or "").strip()
        if str(context.get("phase") or "").strip().upper() != "READY_FOR_PLAN":
            return AgentExecutionPlan(
                task_intent=intent,
                objective=objective,
                status="NOT_READY",
                steps=(),
                approval_required=False,
            )

        steps: list[AgentExecutionPlanStep] = []
        for sequence, raw_code in enumerate(operation_codes, start=1):
            code = str(raw_code or "").strip().casefold()
            definition = cls._DEFINITIONS.get(code)
            if definition is None:
                return AgentExecutionPlan(
                    task_intent=intent,
                    objective=objective,
                    status="UNSUPPORTED_OPERATION",
                    steps=(),
                    approval_required=False,
                )
            steps.append(
                AgentExecutionPlanStep(
                    sequence=sequence,
                    operation_code=definition.code,
                    display_name=definition.display_name,
                    category=definition.category,
                    risk_level=definition.risk_level,
                    route=definition.route,
                    approval_required=True,
                )
            )

        return AgentExecutionPlan(
            task_intent=intent,
            objective=objective,
            status="READY" if steps else "NO_STEPS",
            steps=tuple(steps),
            approval_required=bool(steps),
        )
