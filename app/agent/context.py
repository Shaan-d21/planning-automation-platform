"""Explicit checkpointed task context and deterministic context resolution."""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.agent.canonical import CanonicalActionMode, CanonicalCapability


class EntityResolutionStatus(StrEnum):
    UNRESOLVED = "unresolved"
    EXACT = "exact"
    ONE_CANDIDATE = "one_candidate"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    CATALOG_UNAVAILABLE = "catalog_unavailable"


class ParameterSource(StrEnum):
    CURRENT_TURN = "current_turn"
    USER_CORRECTION = "user_correction"
    ACTIVE_TASK = "active_task"
    RECENT_CONTEXT = "recent_context"
    CONFIGURED_DEFAULT = "configured_default"


class ResolvedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    entity_type: str | None = Field(default=None, max_length=80)
    candidate_name: str | None = Field(default=None, max_length=300)
    canonical_name: str | None = Field(default=None, max_length=300)
    status: EntityResolutionStatus = EntityResolutionStatus.UNRESOLVED
    candidates: list[str] = Field(default_factory=list, max_length=20)
    catalog_source: str | None = Field(default=None, max_length=80)


class PendingSlot(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    prompt: str | None = Field(default=None, max_length=500)
    value_type: str | None = Field(default=None, max_length=80)


class CanonicalTaskContext(BaseModel):
    """Durable source of truth passed through the existing LangGraph state."""

    model_config = ConfigDict(extra="allow")

    context_schema_version: int = 1
    task_id: str
    intent: str
    canonical_capability: CanonicalCapability
    action_mode: CanonicalActionMode
    phase: str
    confidence: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    parameter_sources: dict[str, ParameterSource] = Field(default_factory=dict)
    missing_parameters: list[str] = Field(default_factory=list)
    pending_slot: PendingSlot | None = None
    resolved_entity: ResolvedEntity = Field(default_factory=ResolvedEntity)
    clarification_prompt: str | None = None
    objective: str = ""
    recent_references: dict[str, str] = Field(default_factory=dict)


_INTENT_CAPABILITIES: dict[str, CanonicalCapability] = {
    "MONTH_CLOSE": CanonicalCapability.MULTI_STEP_FLOW,
    "METADATA_LOAD": CanonicalCapability.METADATA_IMPORT,
    "DATA_LOAD": CanonicalCapability.DATA_IMPORT,
    "FORECAST_SEEDING": CanonicalCapability.BUSINESS_RULE,
    "VARIANCE_REPORTING": CanonicalCapability.DATA_REVIEW,
    "RUN_BUSINESS_RULE": CanonicalCapability.BUSINESS_RULE,
    "RUN_DATA_INTEGRATION": CanonicalCapability.DATA_INTEGRATION,
    "RUN_PIPELINE": CanonicalCapability.PIPELINE,
    "RUN_CUBE_REFRESH": CanonicalCapability.CUBE_REFRESH,
    "JOB_STATUS": CanonicalCapability.EXECUTION_HISTORY,
    "CANCEL_OPERATION": CanonicalCapability.UNKNOWN,
    "HELP_EXPLAIN": CanonicalCapability.PLATFORM_DISCOVERY,
    "UNKNOWN": CanonicalCapability.UNKNOWN,
}

_TERMINAL_PHASES = {"COMPLETED", "FAILED", "CANCELLED"}


class AgentContextResolver:
    """Merge explicit current values over compatible checkpointed state.

    This resolver does not infer Oracle identifiers.  It retains a previously
    verified entity only while the same task remains active and records where
    every retained or changed parameter came from.
    """

    @classmethod
    def resolve(
        cls,
        current: dict[str, Any],
        *,
        prior_context: dict[str, Any] | None = None,
        canonical_override: CanonicalCapability | None = None,
        action_override: CanonicalActionMode | None = None,
    ) -> CanonicalTaskContext:
        prior = prior_context if isinstance(prior_context, dict) else {}
        intent = str(current.get("intent") or "UNKNOWN").strip().upper()
        capability = canonical_override or _INTENT_CAPABILITIES.get(
            intent, CanonicalCapability.UNKNOWN
        )
        action_mode = action_override or cls._action_mode(intent, capability)
        prior_capability = cls._capability(prior.get("canonical_capability"))
        compatible = cls._compatible_task(
            current=current,
            prior=prior,
            capability=capability,
            prior_capability=prior_capability,
        )

        current_parameters = current.get("parameters")
        explicit = (
            dict(current_parameters)
            if isinstance(current_parameters, dict)
            else {}
        )
        parameters: dict[str, Any] = {}
        sources: dict[str, ParameterSource] = {}
        if compatible:
            prior_parameters = prior.get("parameters")
            if isinstance(prior_parameters, dict):
                parameters.update(prior_parameters)
                sources.update(
                    {key: ParameterSource.ACTIVE_TASK for key in parameters}
                )
        for key, value in explicit.items():
            source = (
                ParameterSource.USER_CORRECTION
                if key in parameters and parameters[key] != value
                else ParameterSource.CURRENT_TURN
            )
            parameters[key] = value
            sources[key] = source

        missing = [
            str(item).strip()
            for item in current.get("missing_parameters", [])
            if str(item).strip()
        ]
        pending_slot = (
            PendingSlot(
                name=missing[0],
                prompt=str(current.get("clarification_prompt") or "").strip()
                or None,
            )
            if missing
            else None
        )
        entity = cls._entity(prior.get("resolved_entity")) if compatible else None
        if entity is None:
            entity = ResolvedEntity()

        task_id = (
            str(prior.get("task_id") or "").strip()
            if compatible
            else ""
        ) or str(uuid4())
        recent = prior.get("recent_references") if compatible else {}
        recent_references = dict(recent) if isinstance(recent, dict) else {}
        return CanonicalTaskContext(
            task_id=task_id,
            intent=intent,
            canonical_capability=capability,
            action_mode=action_mode,
            phase=str(current.get("phase") or "UNDERSTANDING_REQUEST").upper(),
            confidence=str(current.get("confidence") or "UNSAFE_TO_EXECUTE").upper(),
            parameters=parameters,
            parameter_sources=sources,
            missing_parameters=missing,
            pending_slot=pending_slot,
            resolved_entity=entity,
            clarification_prompt=(
                str(current.get("clarification_prompt") or "").strip() or None
            ),
            objective=str(current.get("objective") or "").strip(),
            recent_references=recent_references,
        )

    @staticmethod
    def _action_mode(
        intent: str,
        capability: CanonicalCapability,
    ) -> CanonicalActionMode:
        if intent == "CANCEL_OPERATION":
            return CanonicalActionMode.CANCEL
        if intent == "JOB_STATUS":
            return CanonicalActionMode.STATUS
        if intent == "HELP_EXPLAIN":
            return CanonicalActionMode.EXPLAIN
        if capability is CanonicalCapability.DATA_REVIEW:
            return CanonicalActionMode.REVIEW
        if capability in {
            CanonicalCapability.UNKNOWN,
            CanonicalCapability.PLATFORM_DISCOVERY,
        }:
            return CanonicalActionMode.UNKNOWN
        return CanonicalActionMode.EXECUTE

    @staticmethod
    def _capability(value: Any) -> CanonicalCapability:
        try:
            return CanonicalCapability(str(value))
        except ValueError:
            return CanonicalCapability.UNKNOWN

    @staticmethod
    def _entity(value: Any) -> ResolvedEntity | None:
        if not isinstance(value, dict):
            return None
        try:
            return ResolvedEntity.model_validate(value)
        except ValueError:
            return None

    @staticmethod
    def _compatible_task(
        *,
        current: dict[str, Any],
        prior: dict[str, Any],
        capability: CanonicalCapability,
        prior_capability: CanonicalCapability,
    ) -> bool:
        if not prior or str(prior.get("phase") or "").upper() in _TERMINAL_PHASES:
            return False
        current_intent = str(current.get("intent") or "UNKNOWN").upper()
        prior_intent = str(prior.get("intent") or "UNKNOWN").upper()
        if current_intent != "UNKNOWN" and current_intent == prior_intent:
            return True
        return (
            capability is not CanonicalCapability.UNKNOWN
            and capability is prior_capability
        )

