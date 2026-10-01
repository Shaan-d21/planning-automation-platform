from __future__ import annotations

from datetime import UTC, datetime

from app.agent.canonical import CanonicalCapability
from app.agent.context import AgentContextResolver, ParameterSource
from app.agent.models import AgentMessage, AgentMessageRole
from app.agent.parameter_updates import AgentParameterDeltaResolver
from app.agent.task_state import AgentTaskInterpreter


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


def test_new_task_keeps_operational_memory_but_not_old_task_parameters() -> None:
    first = AgentContextResolver.resolve(_payload())
    prior = first.model_dump(mode="json")
    prior.update(
        {
            "phase": "COMPLETED",
            "recent_references": {
                "last_execution_id": "execution-1",
                "last_artifact_name": "Import Actuals",
            },
            "previous_action": {
                "operation_code": "data-import",
                "artifact_name": "Import Actuals",
            },
            "current_execution": {
                "execution_id": "execution-1",
                "status": "SUCCESS",
            },
        }
    )

    next_task = AgentContextResolver.resolve(
        _payload(
            intent="RUN_BUSINESS_RULE",
            parameters={"artifact_name": "Aggregate Plan"},
            missing_parameters=[],
            phase="READY_FOR_PLAN",
            resolved_entity={
                "entity_type": "business-rules",
                "candidate_name": "Aggregate Plan",
                "canonical_name": None,
                "status": "unresolved",
                "candidates": [],
                "catalog_source": None,
            },
        ),
        prior_context=prior,
    )

    assert next_task.task_id != first.task_id
    assert next_task.parameters == {"artifact_name": "Aggregate Plan"}
    assert next_task.recent_references["last_execution_id"] == "execution-1"
    assert next_task.previous_action is not None
    assert next_task.current_execution is not None
    assert next_task.resolved_entity.candidate_name == "Aggregate Plan"


def test_parameter_delta_updates_one_slot_without_losing_active_task_values() -> None:
    first = AgentContextResolver.resolve(
        _payload(
            missing_parameters=[],
            phase="READY_FOR_PLAN",
            parameters={
                "scenario": "Actual",
                "period": "Jan",
                "year": "FY27",
                "file": "actual_jan.csv",
            },
        )
    )
    message = AgentMessage(
        message_id=1,
        conversation_id="conversation-1",
        role=AgentMessageRole.USER,
        content="Actually use FY28 instead",
        created_at=datetime.now(UTC),
    )
    interpreted = AgentTaskInterpreter.interpret(
        (message,),
        prior_context=first.model_dump(mode="json"),
    )
    delta = AgentParameterDeltaResolver.resolve(
        message.content,
        first.model_dump(mode="json"),
        interpreted,
    )
    assert delta is not None

    corrected = AgentContextResolver.resolve(
        delta.task.to_payload(),
        prior_context=first.model_dump(mode="json"),
    )

    assert corrected.task_id == first.task_id
    assert corrected.parameters == {
        "scenario": "Actual",
        "period": "Jan",
        "year": "FY28",
        "file": "actual_jan.csv",
    }
    assert corrected.parameter_sources["scenario"] is ParameterSource.ACTIVE_TASK
    assert corrected.parameter_sources["year"] is ParameterSource.USER_CORRECTION


def test_parameter_conflict_clears_old_value_until_user_resolves_it() -> None:
    first = AgentContextResolver.resolve(
        _payload(
            missing_parameters=[],
            phase="READY_FOR_PLAN",
            parameters={
                "scenario": "Actual",
                "period": "Jan",
                "year": "FY27",
                "file": "actual_jan.csv",
            },
        )
    )
    message = AgentMessage(
        message_id=2,
        conversation_id="conversation-1",
        role=AgentMessageRole.USER,
        content="Use FY27 or FY28 instead",
        created_at=datetime.now(UTC),
    )
    interpreted = AgentTaskInterpreter.interpret(
        (message,),
        prior_context=first.model_dump(mode="json"),
    )
    delta = AgentParameterDeltaResolver.resolve(
        message.content,
        first.model_dump(mode="json"),
        interpreted,
    )
    assert delta is not None

    conflicted = AgentContextResolver.resolve(
        delta.task.to_payload(),
        prior_context=first.model_dump(mode="json"),
    )

    assert conflicted.task_id == first.task_id
    assert conflicted.parameters["year"] is None
    assert conflicted.missing_parameters == ["year"]
    assert conflicted.phase == "COLLECTING_INFORMATION"
