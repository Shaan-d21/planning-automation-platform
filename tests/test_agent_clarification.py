"""Tests for deterministic answers to structured artifact choices."""

from app.agent.clarification import (
    ClarificationChoiceResolver,
    ClarificationChoiceStatus,
)
from app.agent.models import AgentClarificationRequest


def _request(*, close_scores: bool = False) -> AgentClarificationRequest:
    return AgentClarificationRequest(
        request_id="choice-1",
        operation_code="pipelines",
        display_name="Pipelines",
        prompt="Choose a Pipeline.",
        options=("PIPE_REVENUE", "PIPE_WORKFORCE", "PIPE_CAPEX"),
        option_labels={
            "PIPE_REVENUE": "Revenue Forecast Pipeline",
            "PIPE_WORKFORCE": "Workforce Planning Pipeline",
            "PIPE_CAPEX": "Capital Expense Pipeline",
        },
        recommendations=(
            {
                "name": "PIPE_REVENUE",
                "confidence": "Strong match",
                "score": 90,
            },
            {
                "name": "PIPE_WORKFORCE",
                "confidence": "Strong match" if close_scores else "Possible match",
                "score": 82 if close_scores else 55,
            },
        ),
    )


def test_exact_identifier_and_display_label_resolve_to_canonical_option() -> None:
    request = _request()

    identifier = ClarificationChoiceResolver.resolve("PIPE_CAPEX", request)
    label = ClarificationChoiceResolver.resolve(
        "Use Revenue Forecast Pipeline",
        request,
    )

    assert identifier.value == "PIPE_CAPEX"
    assert label.value == "PIPE_REVENUE"


def test_ordinal_reply_resolves_against_visible_card_order() -> None:
    result = ClarificationChoiceResolver.resolve("run the second one", _request())

    assert result.status is ClarificationChoiceStatus.SELECTED
    assert result.value == "PIPE_WORKFORCE"
    assert result.reason == "ordinal"


def test_dominant_recommendation_can_be_selected_without_repeating_name() -> None:
    result = ClarificationChoiceResolver.resolve(
        "use the recommended one",
        _request(),
    )

    assert result.value == "PIPE_REVENUE"
    assert result.reason == "dominant_recommendation"


def test_close_recommendations_are_not_guessed() -> None:
    result = ClarificationChoiceResolver.resolve(
        "use the recommended one",
        _request(close_scores=True),
    )

    assert result.status is ClarificationChoiceStatus.UNRESOLVED
    assert result.value is None


def test_unknown_name_and_out_of_range_ordinal_remain_unresolved() -> None:
    unknown = ClarificationChoiceResolver.resolve("Unknown Pipeline", _request())
    ordinal = ClarificationChoiceResolver.resolve("the fifth one", _request())

    assert unknown.status is ClarificationChoiceStatus.UNRESOLVED
    assert unknown.reason == "no_unique_match"
    assert ordinal.status is ClarificationChoiceStatus.UNRESOLVED
    assert ordinal.reason == "ordinal_out_of_range"


def test_explicit_cancel_is_not_treated_as_an_artifact() -> None:
    result = ClarificationChoiceResolver.resolve("never mind", _request())

    assert result.status is ClarificationChoiceStatus.CANCELLED
    assert result.value is None
