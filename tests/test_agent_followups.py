"""Tests for durable, operation-neutral conversational follow-ups."""

from __future__ import annotations

from app.agent.canonical import CanonicalActionMode, CanonicalCapability
from app.agent.followups import AgentFollowUpResolver, FollowUpAct
from app.agent.task_state import AgentTaskIntent


def _completed_rule_context() -> dict[str, object]:
    return {
        "task_id": "task-1",
        "intent": "RUN_BUSINESS_RULE",
        "canonical_capability": "business_rule.run",
        "action_mode": "execute",
        "phase": "COMPLETED",
        "parameters": {"year": "FY27", "scenario": "Actual"},
        "recent_references": {
            "last_operation_code": "business-rules",
            "last_artifact_name": "Calculate Revenue",
            "last_execution_id": "execution-1",
            "last_execution_status": "SUCCESS",
        },
        "previous_action": {
            "task_id": "task-1",
            "operation_code": "business-rules",
            "artifact_name": "Calculate Revenue",
            "task_intent": "RUN_BUSINESS_RULE",
            "canonical_capability": "business_rule.run",
            "parameters": {"year": "FY27", "scenario": "Actual"},
            "completion_status": "SUCCESS",
        },
        "current_execution": {
            "execution_id": "execution-1",
            "operation_code": "business-rules",
            "artifact_name": "Calculate Revenue",
            "status": "SUCCESS",
        },
    }


def test_repeat_last_action_uses_a_candidate_and_requires_governed_execution() -> None:
    result = AgentFollowUpResolver.resolve(
        "Run it again",
        _completed_rule_context(),
    )

    assert result is not None
    assert result.act is FollowUpAct.REPEAT_LAST_ACTION
    assert result.capability is CanonicalCapability.BUSINESS_RULE
    assert result.action_mode is CanonicalActionMode.EXECUTE
    assert result.execution_requested is True
    assert result.task.intent is AgentTaskIntent.RUN_BUSINESS_RULE
    assert result.task.parameters["artifact_name"] == "Calculate Revenue"
    assert result.parameter_sources == {
        "year": "recent_context",
        "scenario": "recent_context",
        "artifact_name": "recent_context",
    }
    assert result.resolved_entity == {
        "entity_type": "business-rules",
        "candidate_name": "Calculate Revenue",
        "canonical_name": None,
        "status": "unresolved",
        "candidates": [],
        "catalog_source": None,
    }


def test_same_action_with_changed_slots_keeps_action_and_applies_correction() -> None:
    result = AgentFollowUpResolver.resolve(
        "Do the same for Forecast in FY28",
        _completed_rule_context(),
    )

    assert result is not None
    assert result.act is FollowUpAct.REPEAT_WITH_CHANGES
    assert result.task.parameters["scenario"] == "Forecast"
    assert result.task.parameters["year"] == "FY28"
    assert result.task.parameters["artifact_name"] == "Calculate Revenue"
    assert result.parameter_sources["scenario"] == "user_correction"
    assert result.parameter_sources["year"] == "user_correction"


def test_status_followup_targets_the_last_authorized_execution() -> None:
    result = AgentFollowUpResolver.resolve(
        "Did it finish?",
        _completed_rule_context(),
    )

    assert result is not None
    assert result.act is FollowUpAct.STATUS_LAST_EXECUTION
    assert result.capability is CanonicalCapability.EXECUTION_HISTORY
    assert result.action_mode is CanonicalActionMode.STATUS
    assert result.task.intent is AgentTaskIntent.JOB_STATUS
    assert result.task.parameters == {"execution_id": "execution-1"}
    assert result.execution_requested is False


def test_failure_followup_uses_explain_mode() -> None:
    context = _completed_rule_context()
    context["phase"] = "FAILED"
    context["current_execution"]["status"] = "FAILED"  # type: ignore[index]

    result = AgentFollowUpResolver.resolve("Why did it fail?", context)

    assert result is not None
    assert result.act is FollowUpAct.EXPLAIN_LAST_FAILURE
    assert result.action_mode is CanonicalActionMode.EXPLAIN
    assert result.task.parameters["execution_id"] == "execution-1"


def test_pronoun_without_durable_reference_is_not_guessed() -> None:
    assert AgentFollowUpResolver.resolve(
        "Run it again",
        {"phase": "COMPLETED", "recent_references": {}},
    ) is None


def test_bare_try_again_remains_diagnostic_and_never_reexecutes() -> None:
    assert AgentFollowUpResolver.resolve(
        "try again",
        _completed_rule_context(),
    ) is None


def test_non_action_again_request_remains_evidence_only() -> None:
    result = AgentFollowUpResolver.resolve(
        "Show me the error again",
        _completed_rule_context(),
    )

    assert result is not None
    assert result.act is FollowUpAct.EXPLAIN_LAST_FAILURE
    assert result.execution_requested is False
