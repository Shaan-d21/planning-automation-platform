from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent.canonical import (
    CANONICAL_CAPABILITY_REGISTRY,
    CanonicalActionMode,
    CanonicalCapability,
    StructuredTaskInterpretation,
    capability_for_operation_code,
    recognize_explicit_capability,
)


def test_every_canonical_capability_has_one_definition() -> None:
    assert set(CANONICAL_CAPABILITY_REGISTRY) == set(CanonicalCapability)


@pytest.mark.parametrize(
    ("operation_code", "capability"),
    [
        ("business-rules", CanonicalCapability.BUSINESS_RULE),
        ("data-integrations", CanonicalCapability.DATA_INTEGRATION),
        ("metadata-import", CanonicalCapability.METADATA_IMPORT),
        ("data-import", CanonicalCapability.DATA_IMPORT),
        ("data-maps", CanonicalCapability.DATA_MAP),
        ("cube-refresh", CanonicalCapability.CUBE_REFRESH),
        ("substitution-variables", CanonicalCapability.SUBSTITUTION_VARIABLE),
        ("user-variables", CanonicalCapability.USER_VARIABLE),
    ],
)
def test_existing_operation_codes_have_canonical_capabilities(
    operation_code: str,
    capability: CanonicalCapability,
) -> None:
    assert capability_for_operation_code(operation_code) is capability


def test_unknown_operation_code_is_not_guessed() -> None:
    assert (
        capability_for_operation_code("invented-operation")
        is CanonicalCapability.UNKNOWN
    )


def test_structured_interpretation_rejects_executable_or_extra_fields() -> None:
    with pytest.raises(ValidationError):
        StructuredTaskInterpretation.model_validate(
            {
                "capability": "business_rule.run",
                "action_mode": "execute",
                "execution_requested": True,
                "payload": {"jobType": "RULES"},
            }
        )


def test_structured_interpretation_accepts_only_candidate_values() -> None:
    result = StructuredTaskInterpretation.model_validate(
        {
            "capability": "business_rule.run",
            "action_mode": CanonicalActionMode.EXECUTE,
            "execution_requested": True,
            "entity_type": "business_rule",
            "entity_name": "Aggregate Plan",
            "parameters": {"Years": "FY27"},
            "confidence": "medium",
            "objective": "Run Aggregate Plan for FY27",
        }
    )

    assert result.entity_name == "Aggregate Plan"
    assert result.parameters == {"Years": "FY27"}


@pytest.mark.parametrize(
    ("prompt", "capability"),
    [
        ("Push data using Revenue to Reporting Data Map", CanonicalCapability.DATA_MAP),
        ("Run Import Products metadata job", CanonicalCapability.METADATA_IMPORT),
    ],
)
def test_explicit_platform_nouns_are_recognized_without_a_model(
    prompt: str,
    capability: CanonicalCapability,
) -> None:
    result = recognize_explicit_capability(prompt)

    assert result is not None
    assert result.capability is capability
    assert result.execution_requested is True
