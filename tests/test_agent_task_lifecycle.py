"""Tests for authoritative agent task lifecycle reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime

from app.agent.models import AgentActionDecision
from app.agent.task_lifecycle import (
    context_needs_focused_reply,
    phase_for_interrupt,
    reconcile_task_context,
)


def _decision(
    *,
    task_id: str = "task-1",
    outcome: str = "SUBMITTED",
    completion: str | None = None,
    execution_id: str | None = "execution-1",
) -> AgentActionDecision:
    now = datetime.now(UTC)
    return AgentActionDecision(
        decision_id="decision-1",
        request_id="request-1",
        conversation_id="conversation-1",
        actor_user_id=1,
        actor_username="planner",
        operation_code="business-rules",
        artifact_name="Aggregate Plan",
        decision="APPROVE" if outcome != "REJECTED" else "REJECT",
        payload_checksum="checksum",
        payload_snapshot={"task_id": task_id},
        outcome_status=outcome,
        execution_id=execution_id,
        failure_summary=None,
        decided_at=now,
        finalized_at=now,
        completion_status=completion,
    )


def _context(task_id: str = "task-1") -> dict[str, object]:
    return {
        "task_id": task_id,
        "intent": "RUN_BUSINESS_RULE",
        "phase": "READY_FOR_PLAN",
        "missing_parameters": ["artifact_name"],
        "pending_slot": {"name": "artifact_name"},
        "recent_references": {},
    }


def test_interrupts_define_durable_waiting_phases() -> None:
    assert phase_for_interrupt("operation_artifact_selection") == (
        "AWAITING_USER_INPUT"
    )
    assert phase_for_interrupt("operation_input_collection") == (
        "AWAITING_USER_INPUT"
    )
    assert phase_for_interrupt("governed_operation_preparation") == (
        "AWAITING_APPROVAL"
    )
    assert phase_for_interrupt("unknown") is None


def test_only_tasks_awaiting_a_user_reply_capture_unclear_followups() -> None:
    assert context_needs_focused_reply({"phase": "COLLECTING_INFORMATION"})
    assert context_needs_focused_reply({"phase": "AWAITING_USER_INPUT"})
    assert context_needs_focused_reply({"phase": "AWAITING_APPROVAL"})
    assert context_needs_focused_reply(
        {"phase": "READY_FOR_PLAN"},
        "yes prepare now",
    )
    assert not context_needs_focused_reply(
        {"phase": "READY_FOR_PLAN"},
        "show me another report",
    )
    assert not context_needs_focused_reply({"phase": "WAITING_FOR_ORACLE"})
    assert not context_needs_focused_reply({"phase": "COMPLETED"})


def test_submitted_decision_moves_matching_task_to_waiting_for_oracle() -> None:
    reconciled = reconcile_task_context(_context(), (_decision(),))

    assert reconciled is not None
    assert reconciled["phase"] == "WAITING_FOR_ORACLE"
    assert reconciled["missing_parameters"] == []
    assert reconciled["pending_slot"] is None
    assert reconciled["current_execution"] == {
        "execution_id": "execution-1",
        "operation_code": "business-rules",
        "artifact_name": "Aggregate Plan",
        "status": "SUBMITTED",
    }


def test_terminal_decision_closes_task_and_preserves_operational_memory() -> None:
    reconciled = reconcile_task_context(
        _context(),
        (_decision(completion="SUCCESS"),),
    )

    assert reconciled is not None
    assert reconciled["phase"] == "COMPLETED"
    assert reconciled["recent_references"] == {
        "last_operation_code": "business-rules",
        "last_artifact_name": "Aggregate Plan",
        "last_execution_id": "execution-1",
        "last_execution_status": "SUCCESS",
    }
    assert reconciled["previous_action"]["artifact_name"] == "Aggregate Plan"
    assert reconciled["previous_action"]["task_intent"] == "RUN_BUSINESS_RULE"


def test_unrelated_decision_does_not_transition_new_task() -> None:
    reconciled = reconcile_task_context(
        _context(task_id="new-task"),
        (_decision(task_id="old-task", completion="SUCCESS"),),
    )

    assert reconciled is not None
    assert reconciled["phase"] == "READY_FOR_PLAN"
    assert reconciled["recent_references"]["last_artifact_name"] == (
        "Aggregate Plan"
    )


def test_rejected_decision_cancels_matching_task() -> None:
    reconciled = reconcile_task_context(
        _context(),
        (_decision(outcome="REJECTED", execution_id=None),),
    )

    assert reconciled is not None
    assert reconciled["phase"] == "CANCELLED"
    assert reconciled["current_execution"] is None
