"""Canonical language contract for the EPM assistant.

The values in this module describe *what* the user means.  They do not build
Oracle requests, select unverified artifacts, or authorize execution.  Each
executable capability maps back to an existing governed operation code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from types import MappingProxyType
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CanonicalCapability(StrEnum):
    """Stable capability vocabulary shared by interpretation and routing."""

    PLATFORM_DISCOVERY = "platform.discovery"
    EXECUTION_HISTORY = "execution.history"
    DATA_REVIEW = "data.review"
    REPORT_EXPORT = "report.export"
    CUBE_REFRESH = "cube.refresh"
    SUBSTITUTION_VARIABLE = "variable.substitution"
    USER_VARIABLE = "variable.user"
    DATA_IMPORT = "data.import"
    METADATA_IMPORT = "metadata.import"
    PIPELINE = "pipeline.run"
    DATA_INTEGRATION = "data.integration"
    BUSINESS_RULE = "business_rule.run"
    DATA_MAP = "data_map.run"
    SCHEDULE = "schedule.manage"
    MULTI_STEP_FLOW = "flow.multi_step"
    UNKNOWN = "unknown"


class CanonicalActionMode(StrEnum):
    """What the user wants to do with a capability."""

    EXECUTE = "execute"
    LIST = "list"
    STATUS = "status"
    REVIEW = "review"
    EXPLAIN = "explain"
    UPDATE = "update"
    EXPORT = "export"
    CANCEL = "cancel"
    UNKNOWN = "unknown"


class InterpretationConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class StructuredTaskInterpretation(BaseModel):
    """Strict, non-executable result accepted from a language model.

    Entity names and parameters are only user-provided candidates.  The
    capability gateway must still resolve identifiers and normalize every
    value before an approval card can be produced.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    capability: CanonicalCapability
    action_mode: CanonicalActionMode
    execution_requested: bool
    negated: bool = False
    entity_type: str | None = Field(default=None, max_length=80)
    entity_name: str | None = Field(default=None, max_length=300)
    parameters: dict[str, str | int | float | bool | list[str]] = Field(
        default_factory=dict
    )
    corrections: dict[str, str | int | float | bool | list[str]] = Field(
        default_factory=dict
    )
    referenced_entity: str | None = Field(default=None, max_length=80)
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    confidence: InterpretationConfidence = InterpretationConfidence.LOW
    objective: str = Field(default="", max_length=1000)

    @field_validator("parameters", "corrections")
    @classmethod
    def _bounded_parameter_names(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        if len(value) > 40:
            raise ValueError("Too many interpreted parameters.")
        for key in value:
            if not key or len(key) > 80:
                raise ValueError("Interpreted parameter names must be 1-80 characters.")
        return value

    @field_validator("missing_information")
    @classmethod
    def _bounded_missing_names(cls, value: list[str]) -> list[str]:
        for item in value:
            if not item or len(item) > 80:
                raise ValueError("Missing-information names must be 1-80 characters.")
        return value


@dataclass(frozen=True, slots=True)
class CanonicalCapabilityDefinition:
    """Translation from language capability to existing platform control."""

    capability: CanonicalCapability
    operation_code: str | None
    artifact_type: str | None
    mutating: bool
    approval_required: bool
    required_slots: tuple[str, ...] = ()


_DEFINITIONS = (
    CanonicalCapabilityDefinition(
        CanonicalCapability.PLATFORM_DISCOVERY, None, None, False, False
    ),
    CanonicalCapabilityDefinition(
        CanonicalCapability.EXECUTION_HISTORY, None, "execution", False, False
    ),
    CanonicalCapabilityDefinition(
        CanonicalCapability.DATA_REVIEW, None, "cube", False, False
    ),
    CanonicalCapabilityDefinition(
        CanonicalCapability.REPORT_EXPORT,
        "report-generation",
        "saved_data_view",
        False,
        False,
        ("artifact_name",),
    ),
    CanonicalCapabilityDefinition(
        CanonicalCapability.CUBE_REFRESH,
        "cube-refresh",
        "cube_refresh_job",
        True,
        True,
        ("artifact_name",),
    ),
    CanonicalCapabilityDefinition(
        CanonicalCapability.SUBSTITUTION_VARIABLE,
        "substitution-variables",
        "substitution_variable",
        True,
        True,
        ("artifact_name", "value"),
    ),
    CanonicalCapabilityDefinition(
        CanonicalCapability.USER_VARIABLE,
        "user-variables",
        "user_variable",
        True,
        True,
        ("artifact_name", "value"),
    ),
    CanonicalCapabilityDefinition(
        CanonicalCapability.DATA_IMPORT,
        "data-import",
        "data_import_job",
        True,
        True,
        ("artifact_name", "file"),
    ),
    CanonicalCapabilityDefinition(
        CanonicalCapability.METADATA_IMPORT,
        "metadata-import",
        "metadata_import_job",
        True,
        True,
        ("artifact_name", "file"),
    ),
    CanonicalCapabilityDefinition(
        CanonicalCapability.PIPELINE,
        "pipelines",
        "pipeline",
        True,
        True,
        ("artifact_name",),
    ),
    CanonicalCapabilityDefinition(
        CanonicalCapability.DATA_INTEGRATION,
        "data-integrations",
        "data_integration",
        True,
        True,
        ("artifact_name",),
    ),
    CanonicalCapabilityDefinition(
        CanonicalCapability.BUSINESS_RULE,
        "business-rules",
        "business_rule",
        True,
        True,
        ("artifact_name",),
    ),
    CanonicalCapabilityDefinition(
        CanonicalCapability.DATA_MAP,
        "data-maps",
        "data_map",
        True,
        True,
        ("artifact_name",),
    ),
    CanonicalCapabilityDefinition(
        CanonicalCapability.SCHEDULE, None, "schedule", True, True
    ),
    CanonicalCapabilityDefinition(
        CanonicalCapability.MULTI_STEP_FLOW, None, "operation_sequence", True, True
    ),
    CanonicalCapabilityDefinition(
        CanonicalCapability.UNKNOWN, None, None, False, False
    ),
)

CANONICAL_CAPABILITY_REGISTRY: Mapping[
    CanonicalCapability, CanonicalCapabilityDefinition
] = MappingProxyType({item.capability: item for item in _DEFINITIONS})

_BY_OPERATION_CODE: Mapping[str, CanonicalCapabilityDefinition] = MappingProxyType(
    {
        item.operation_code: item
        for item in _DEFINITIONS
        if item.operation_code is not None
    }
)


def capability_definition(
    capability: CanonicalCapability | str,
) -> CanonicalCapabilityDefinition:
    """Return one definition or the safe UNKNOWN definition."""
    try:
        normalized = CanonicalCapability(str(capability))
    except ValueError:
        normalized = CanonicalCapability.UNKNOWN
    return CANONICAL_CAPABILITY_REGISTRY[normalized]


def capability_for_operation_code(operation_code: str) -> CanonicalCapability:
    """Translate an existing operation code without guessing."""
    definition = _BY_OPERATION_CODE.get(str(operation_code).strip().casefold())
    return (
        definition.capability
        if definition is not None
        else CanonicalCapability.UNKNOWN
    )


_EXPLICIT_CAPABILITY_TERMS: tuple[
    tuple[CanonicalCapability, tuple[str, ...]], ...
] = (
    (CanonicalCapability.BUSINESS_RULE, ("business rule", "calculation rule", "calc rule")),
    (CanonicalCapability.DATA_MAP, ("data map", "smart push", "smartpush")),
    (CanonicalCapability.DATA_INTEGRATION, ("data integration",)),
    (CanonicalCapability.METADATA_IMPORT, ("metadata import", "metadata job")),
    (CanonicalCapability.DATA_IMPORT, ("data import", "import data job")),
    (CanonicalCapability.PIPELINE, ("pipeline",)),
    (CanonicalCapability.CUBE_REFRESH, ("cube refresh", "refresh job")),
    (CanonicalCapability.SUBSTITUTION_VARIABLE, ("substitution variable",)),
    (CanonicalCapability.USER_VARIABLE, ("user variable",)),
)


def recognize_explicit_capability(
    prompt: str,
) -> StructuredTaskInterpretation | None:
    """Recognize explicit platform nouns without interpreting business prose.

    This is intentionally a small vocabulary of canonical product concepts,
    not a sentence catalog. Implicit goals and multilingual paraphrases remain
    the semantic interpreter's job.
    """
    normalized = " ".join(str(prompt or "").casefold().split())
    matches = [
        capability
        for capability, terms in _EXPLICIT_CAPABILITY_TERMS
        if any(term in normalized for term in terms)
    ]
    matches = list(dict.fromkeys(matches))
    if len(matches) != 1:
        return None
    negated = bool(
        re.search(
            r"\b(?:do not|don't|dont|never|without)\b.{0,35}"
            r"\b(?:run|execute|start|change|update|import|load|push|refresh)\b",
            normalized,
        )
    )
    if re.search(r"\b(?:list|show|which|available)\b", normalized):
        action = CanonicalActionMode.LIST
    elif re.search(r"\b(?:explain|what is|how does|why)\b", normalized) or negated:
        action = CanonicalActionMode.EXPLAIN
    elif matches[0] in {
        CanonicalCapability.SUBSTITUTION_VARIABLE,
        CanonicalCapability.USER_VARIABLE,
    } and re.search(r"\b(?:change|set|update|assign|create)\b", normalized):
        action = CanonicalActionMode.UPDATE
    elif re.search(
        r"\b(?:run|execute|start|prepare|load|import|push|refresh)\b",
        normalized,
    ):
        action = CanonicalActionMode.EXECUTE
    else:
        return None
    execution_requested = action in {
        CanonicalActionMode.EXECUTE,
        CanonicalActionMode.UPDATE,
    } and not negated
    return StructuredTaskInterpretation(
        capability=matches[0],
        action_mode=action,
        execution_requested=execution_requested,
        negated=negated,
        confidence=InterpretationConfidence.HIGH,
        objective=str(prompt or "").strip(),
    )
