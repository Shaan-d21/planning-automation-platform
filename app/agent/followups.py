"""Structured follow-up resolution over durable agent task state.

The resolver recognizes a small set of dialogue acts rather than maintaining
an ever-growing sentence catalog. It never resolves an Oracle artifact on its
own: retained names remain candidates and must pass through the live catalog
and the existing governed approval workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
import re
from typing import Any

from app.agent.canonical import (
    CanonicalActionMode,
    CanonicalCapability,
    capability_for_operation_code,
)
from app.agent.context import EntityResolutionStatus
from app.agent.task_state import (
    AgentTaskConfidence,
    AgentTaskIntent,
    AgentTaskInterpreter,
    AgentTaskPhase,
    AgentTaskUnderstanding,
)


class FollowUpAct(StrEnum):
    REPEAT_LAST_ACTION = "repeat_last_action"
    REPEAT_WITH_CHANGES = "repeat_with_changes"
    STATUS_LAST_EXECUTION = "status_last_execution"
    EXPLAIN_LAST_FAILURE = "explain_last_failure"


@dataclass(frozen=True, slots=True)
class FollowUpResolution:
    act: FollowUpAct
    task: AgentTaskUnderstanding
    capability: CanonicalCapability
    action_mode: CanonicalActionMode
    execution_requested: bool
    referenced_entity: str
    resolved_entity: dict[str, Any] | None = None
    parameter_sources: dict[str, str] | None = None

    def apply(self, context: dict[str, Any]) -> None:
        context["dialogue_act"] = self.act.value
        context["execution_requested"] = self.execution_requested
        context["negated"] = False
        context["referenced_entity"] = self.referenced_entity
        if self.parameter_sources is not None:
            context["parameter_sources"] = dict(self.parameter_sources)
        if self.resolved_entity is not None:
            context["resolved_entity"] = dict(self.resolved_entity)


class AgentFollowUpResolver:
    """Resolve references only when durable conversation evidence exists."""

    _REPEAT = re.compile(
        r"(?:\b(?:repeat|rerun|re-run)\b|"
        r"\b(?:run|execute|start|do|use|prepare|calculate|load|import|push|refresh)"
        r"\b.{0,40}\bagain\b|^\s*again[.!]?\s*$|"
        r"^\s*(?:run|execute|start|use|do)\s+"
        r"(?:it|that|this|the\s+same|the\s+previous|the\s+last)"
        r"(?:\s+(?:one|action|operation|job|rule|pipeline|integration))?\b)",
        re.IGNORECASE,
    )
    _SAME_WITH_CHANGES = re.compile(
        r"^\s*(?:do|run|execute|use|prepare)?\s*"
        r"(?:the\s+)?same(?:\s+(?:thing|action|operation|one))?\s+"
        r"(?:for|with|using|but|except)\b",
        re.IGNORECASE,
    )
    _STATUS = re.compile(
        r"\b(?:status|finished|done|completed?|running|started)\b|"
        r"\bwhat\s+happened\b|\bdid\s+(?:it|that|the\s+job)\b|"
        r"\bis\s+(?:it|that|the\s+job)\b",
        re.IGNORECASE,
    )
    _FAILURE = re.compile(
        r"\bwhy\b.{0,50}\b(?:fail(?:ed|ure)?|error|wrong)\b|"
        r"\bwhat\s+went\s+wrong\b|\b(?:show|explain)\b.{0,30}\b(?:error|failure)\b",
        re.IGNORECASE,
    )

    _INTENT_BY_OPERATION = {
        "business-rules": AgentTaskIntent.RUN_BUSINESS_RULE,
        "pipelines": AgentTaskIntent.RUN_PIPELINE,
        "data-integrations": AgentTaskIntent.RUN_DATA_INTEGRATION,
        "cube-refresh": AgentTaskIntent.RUN_CUBE_REFRESH,
        "data-import": AgentTaskIntent.DATA_LOAD,
        "metadata-import": AgentTaskIntent.METADATA_LOAD,
    }

    @classmethod
    def resolve(
        cls,
        prompt: str,
        prior_context: dict[str, Any] | None,
        *,
        today: date | None = None,
    ) -> FollowUpResolution | None:
        if not isinstance(prior_context, dict):
            return None
        normalized = " ".join(str(prompt or "").split())
        if not normalized:
            return None

        # A bare recovery request remains an evidence/diagnostic follow-up.
        # Re-execution requires an explicit action verb such as run/rerun so
        # an error-screen "try again" can never submit another Oracle job.
        if re.fullmatch(
            r"\s*(?:please\s+)?(?:try\s+again|retry)(?:\s+it)?[.!]?\s*",
            normalized,
            re.IGNORECASE,
        ):
            return None

        execution = cls._execution_reference(prior_context)
        previous = prior_context.get("previous_action")
        previous_action = previous if isinstance(previous, dict) else None

        if execution and cls._FAILURE.search(normalized):
            return cls._execution_follow_up(
                prompt=normalized,
                execution=execution,
                act=FollowUpAct.EXPLAIN_LAST_FAILURE,
                action_mode=CanonicalActionMode.EXPLAIN,
            )
        if execution and cls._STATUS.search(normalized):
            return cls._execution_follow_up(
                prompt=normalized,
                execution=execution,
                act=FollowUpAct.STATUS_LAST_EXECUTION,
                action_mode=CanonicalActionMode.STATUS,
            )
        if previous_action and (
            cls._REPEAT.search(normalized)
            or cls._SAME_WITH_CHANGES.search(normalized)
        ):
            return cls._repeat_follow_up(
                prompt=normalized,
                prior_context=prior_context,
                previous_action=previous_action,
                today=today,
            )
        return None

    @classmethod
    def _execution_follow_up(
        cls,
        *,
        prompt: str,
        execution: dict[str, Any],
        act: FollowUpAct,
        action_mode: CanonicalActionMode,
    ) -> FollowUpResolution:
        execution_id = str(execution.get("execution_id") or "").strip()
        task = AgentTaskUnderstanding(
            intent=AgentTaskIntent.JOB_STATUS,
            phase=AgentTaskPhase.READY_FOR_PLAN,
            confidence=AgentTaskConfidence.HIGH_CONFIDENCE,
            parameters={"execution_id": execution_id},
            objective=prompt,
        )
        return FollowUpResolution(
            act=act,
            task=task,
            capability=CanonicalCapability.EXECUTION_HISTORY,
            action_mode=action_mode,
            execution_requested=False,
            referenced_entity="last_execution",
            parameter_sources={"execution_id": "recent_context"},
        )

    @classmethod
    def _repeat_follow_up(
        cls,
        *,
        prompt: str,
        prior_context: dict[str, Any],
        previous_action: dict[str, Any],
        today: date | None,
    ) -> FollowUpResolution | None:
        operation_code = str(previous_action.get("operation_code") or "").strip()
        capability = capability_for_operation_code(operation_code)
        if capability is CanonicalCapability.UNKNOWN:
            return None
        intent = cls._intent(previous_action, operation_code)
        parameters = cls._retained_parameters(previous_action, prior_context)
        corrections = AgentTaskInterpreter.extract_parameters_for_intent(
            prompt,
            intent,
            today=today,
        )
        parameters.update(corrections)
        artifact_name = str(previous_action.get("artifact_name") or "").strip()
        if artifact_name:
            parameters["artifact_name"] = artifact_name
        if operation_code == "data-integrations" and intent is AgentTaskIntent.DATA_LOAD:
            parameters["load_method"] = "DATA_INTEGRATION"
        elif operation_code == "data-import" and intent is AgentTaskIntent.DATA_LOAD:
            parameters["load_method"] = "PLANNING_IMPORT"

        changed = bool(corrections) or bool(cls._SAME_WITH_CHANGES.search(prompt))
        act = (
            FollowUpAct.REPEAT_WITH_CHANGES
            if changed
            else FollowUpAct.REPEAT_LAST_ACTION
        )
        objective = (
            f"Run {artifact_name} again with the requested changes."
            if artifact_name and changed
            else f"Run {artifact_name} again."
            if artifact_name
            else "Repeat the previous governed Oracle operation."
        )
        task = AgentTaskUnderstanding(
            intent=intent,
            phase=AgentTaskPhase.READY_FOR_PLAN,
            confidence=AgentTaskConfidence.HIGH_CONFIDENCE,
            parameters=parameters,
            objective=objective,
        )
        entity = (
            {
                "entity_type": operation_code,
                "candidate_name": artifact_name,
                "canonical_name": None,
                "status": EntityResolutionStatus.UNRESOLVED.value,
                "candidates": [],
                "catalog_source": None,
            }
            if artifact_name
            else None
        )
        return FollowUpResolution(
            act=act,
            task=task,
            capability=capability,
            action_mode=CanonicalActionMode.EXECUTE,
            execution_requested=True,
            referenced_entity="previous_action",
            resolved_entity=entity,
            parameter_sources={
                key: (
                    "user_correction" if key in corrections else "recent_context"
                )
                for key in parameters
            },
        )

    @classmethod
    def _intent(
        cls,
        previous_action: dict[str, Any],
        operation_code: str,
    ) -> AgentTaskIntent:
        try:
            return AgentTaskIntent(
                str(previous_action.get("task_intent") or "UNKNOWN").upper()
            )
        except ValueError:
            return cls._INTENT_BY_OPERATION.get(
                operation_code,
                AgentTaskIntent.UNKNOWN,
            )

    @staticmethod
    def _retained_parameters(
        previous_action: dict[str, Any],
        prior_context: dict[str, Any],
    ) -> dict[str, Any]:
        stored = previous_action.get("parameters")
        if isinstance(stored, dict):
            return dict(stored)
        prior = prior_context.get("parameters")
        return dict(prior) if isinstance(prior, dict) else {}

    @staticmethod
    def _execution_reference(
        prior_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        current = prior_context.get("current_execution")
        if isinstance(current, dict) and current.get("execution_id"):
            return current
        recent = prior_context.get("recent_references")
        if not isinstance(recent, dict):
            return None
        execution_id = str(recent.get("last_execution_id") or "").strip()
        if not execution_id:
            return None
        return {
            "execution_id": execution_id,
            "operation_code": recent.get("last_operation_code"),
            "artifact_name": recent.get("last_artifact_name"),
            "status": recent.get("last_execution_status"),
        }
