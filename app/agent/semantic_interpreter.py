"""Bounded model-assisted interpretation for language parser gaps."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from app.agent.canonical import (
    CanonicalActionMode,
    CanonicalCapability,
    StructuredTaskInterpretation,
)
from app.agent.models import AgentMessage, AgentToolDefinition
from app.agent.provider import AgentProvider
from app.agent.task_state import (
    AgentTaskConfidence,
    AgentTaskIntent,
    AgentTaskPhase,
    AgentTaskUnderstanding,
)
from app.utils.exceptions import AgentProviderError


_INTERPRET_TOOL = "record_task_interpretation"


class AgentSemanticInterpreter:
    """Ask the configured model for strict semantics, never execution data."""

    _INSTRUCTION = """You interpret the user's Oracle EPM business intent.
Return exactly one record_task_interpretation tool call. Use only the enum
capabilities and action modes in its schema. Extract names and values only when
the user stated them; they remain unverified candidates. Never invent an
artifact, environment, member, file, job result, permission, or default. A
question, explanation, list request, hypothetical, or negated instruction is
not an execution request. Set negated=true when the user says not to run or
change something. Resolve short references such as 'it', 'that rule', 'the
second one', and corrections from the supplied active task only when the
reference is unambiguous. If the capability is unclear, use unknown. Do not
produce an Oracle REST payload or approval decision."""

    @classmethod
    def interpret(
        cls,
        *,
        provider: AgentProvider,
        messages: Sequence[AgentMessage],
        prior_context: dict[str, Any] | None,
        logger: logging.Logger | None = None,
    ) -> StructuredTaskInterpretation | None:
        generate = getattr(provider, "generate", None)
        if not callable(generate):
            return None
        log = logger or logging.getLogger(__name__)
        active_context = json.dumps(
            cls._safe_prior_context(prior_context),
            ensure_ascii=True,
            separators=(",", ":"),
        )[:4_000]
        instruction = (
            f"{cls._INSTRUCTION}\n\n"
            "Active structured task context (may be empty):\n"
            f"{active_context}"
        )
        tool = AgentToolDefinition(
            name=_INTERPRET_TOOL,
            description=(
                "Record a non-executable canonical interpretation of the "
                "current user turn."
            ),
            parameters_schema=StructuredTaskInterpretation.model_json_schema(),
        )
        try:
            turn = generate(
                messages=tuple(messages[-8:]),
                system_instruction=instruction,
                tools=(tool,),
                provider_exchange=(),
                required_tool_name=_INTERPRET_TOOL,
            )
        except AgentProviderError as exc:
            log.warning("Semantic interpretation was unavailable: %s", exc)
            return None
        calls = tuple(
            call for call in turn.tool_calls if call.name == _INTERPRET_TOOL
        )
        if len(calls) != 1:
            log.info(
                "Semantic interpretation returned %d canonical calls; ignored.",
                len(calls),
            )
            return None
        try:
            return StructuredTaskInterpretation.model_validate(calls[0].arguments)
        except ValidationError as exc:
            log.warning("Semantic interpretation failed schema validation: %s", exc)
            return None

    @staticmethod
    def _safe_prior_context(value: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        allowed = {
            "task_id",
            "intent",
            "canonical_capability",
            "action_mode",
            "phase",
            "parameters",
            "missing_parameters",
            "pending_slot",
            "resolved_entity",
            "objective",
            "recent_references",
        }
        return {key: item for key, item in value.items() if key in allowed}


_LEGACY_INTENTS = {
    CanonicalCapability.EXECUTION_HISTORY: AgentTaskIntent.JOB_STATUS,
    CanonicalCapability.DATA_REVIEW: AgentTaskIntent.VARIANCE_REPORTING,
    CanonicalCapability.CUBE_REFRESH: AgentTaskIntent.RUN_CUBE_REFRESH,
    CanonicalCapability.DATA_IMPORT: AgentTaskIntent.DATA_LOAD,
    CanonicalCapability.METADATA_IMPORT: AgentTaskIntent.METADATA_LOAD,
    CanonicalCapability.PIPELINE: AgentTaskIntent.RUN_PIPELINE,
    CanonicalCapability.DATA_INTEGRATION: AgentTaskIntent.RUN_DATA_INTEGRATION,
    CanonicalCapability.BUSINESS_RULE: AgentTaskIntent.RUN_BUSINESS_RULE,
    CanonicalCapability.MULTI_STEP_FLOW: AgentTaskIntent.MONTH_CLOSE,
}


def semantic_task_understanding(
    interpretation: StructuredTaskInterpretation,
    *,
    fallback_objective: str,
) -> AgentTaskUnderstanding:
    """Bridge canonical semantics into legacy state during migration."""
    if interpretation.negated:
        intent = AgentTaskIntent.HELP_EXPLAIN
    else:
        intent = _LEGACY_INTENTS.get(
            interpretation.capability,
            AgentTaskIntent.UNKNOWN,
        )
    executable = (
        interpretation.execution_requested
        and not interpretation.negated
        and interpretation.action_mode
        in {CanonicalActionMode.EXECUTE, CanonicalActionMode.UPDATE}
    )
    if not executable and intent not in {
        AgentTaskIntent.JOB_STATUS,
        AgentTaskIntent.VARIANCE_REPORTING,
        AgentTaskIntent.HELP_EXPLAIN,
    }:
        intent = AgentTaskIntent.UNKNOWN
    parameters = dict(interpretation.parameters)
    parameters.update(interpretation.corrections)
    if interpretation.entity_name:
        parameters.setdefault("artifact_name", interpretation.entity_name)
    missing = tuple(interpretation.missing_information)
    if interpretation.negated:
        phase = AgentTaskPhase.UNDERSTANDING_REQUEST
        confidence = AgentTaskConfidence.HIGH_CONFIDENCE
    elif executable and missing:
        phase = AgentTaskPhase.COLLECTING_INFORMATION
        confidence = AgentTaskConfidence.NEEDS_CLARIFICATION
    elif executable:
        phase = AgentTaskPhase.READY_FOR_PLAN
        confidence = (
            AgentTaskConfidence.HIGH_CONFIDENCE
            if interpretation.confidence.value == "high"
            else AgentTaskConfidence.NEEDS_CLARIFICATION
        )
    else:
        phase = AgentTaskPhase.UNDERSTANDING_REQUEST
        confidence = AgentTaskConfidence.NEEDS_CLARIFICATION
    return AgentTaskUnderstanding(
        intent=intent,
        phase=phase,
        confidence=confidence,
        parameters=parameters,
        missing_parameters=missing,
        clarification_prompt=(
            f"Please provide {missing[0].replace('_', ' ')}."
            if missing else None
        ),
        objective=interpretation.objective or fallback_objective,
    )
