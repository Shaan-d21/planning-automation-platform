from __future__ import annotations

from app.agent.canonical import CanonicalCapability
from app.agent.context import AgentContextResolver, ParameterSource


def _payload(**overrides):
    payload = {
        "intent": "DATA_LOAD",
        "phase": "COLLECTING_INFORMATION",
        "confidence": "NEEDS_CLARIFICATION",
        "parameters": {"year": "FY27", "period": "Jan"},
        "missing_parameters": ["file"],
        "clarification_prompt": "Which file should be loaded?",
        "objective": "Load January FY27 actuals.",
    }
    payload.update(overrides)
    return payload


def test_context_assigns_task_identity_and_pending_slot() -> None:
    context = AgentContextResolver.resolve(_payload())

    assert context.task_id
    assert context.canonical_capability is CanonicalCapability.DATA_IMPORT
    assert context.pending_slot is not None
    assert context.pending_slot.name == "file"
    assert context.parameter_sources == {
        "year": ParameterSource.CURRENT_TURN,
        "period": ParameterSource.CURRENT_TURN,
    }


def test_compatible_follow_up_retains_task_and_records_correction() -> None:
    first = AgentContextResolver.resolve(_payload())
    follow_up = AgentContextResolver.resolve(
        _payload(
            parameters={"period": "Feb", "file": "Actual_Feb.csv"},
            missing_parameters=[],
            clarification_prompt=None,
            phase="READY_FOR_PLAN",
        ),
        prior_context=first.model_dump(mode="json"),
    )

    assert follow_up.task_id == first.task_id
    assert follow_up.parameters == {
        "year": "FY27",
        "period": "Feb",
        "file": "Actual_Feb.csv",
    }
    assert follow_up.parameter_sources["year"] is ParameterSource.ACTIVE_TASK
    assert follow_up.parameter_sources["period"] is ParameterSource.USER_CORRECTION
    assert follow_up.parameter_sources["file"] is ParameterSource.CURRENT_TURN
    assert follow_up.pending_slot is None


def test_different_capability_starts_a_new_task_without_parameter_leakage() -> None:
    first = AgentContextResolver.resolve(_payload())
    next_task = AgentContextResolver.resolve(
        _payload(
            intent="RUN_BUSINESS_RULE",
            parameters={"artifact_name": "Aggregate Plan"},
            missing_parameters=[],
            phase="READY_FOR_PLAN",
            objective="Run Aggregate Plan.",
        ),
        prior_context=first.model_dump(mode="json"),
    )

    assert next_task.task_id != first.task_id
    assert next_task.canonical_capability is CanonicalCapability.BUSINESS_RULE
    assert next_task.parameters == {"artifact_name": "Aggregate Plan"}

