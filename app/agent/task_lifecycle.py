"""Authoritative task lifecycle reconciliation for agent conversations.

LangGraph owns pending interaction state while action decisions and the
execution queue own submitted/terminal operation state. This module reduces
those durable facts into one checkpoint-safe task context before a new user
turn is interpreted.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.agent.models import AgentActionDecision


_FOCUSED_REPLY_PHASES = frozenset(
    {
        "COLLECTING_INFORMATION",
        "AWAITING_USER_INPUT",
        "AWAITING_APPROVAL",
    }
)
_TERMINAL_PHASES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


def context_needs_focused_reply(
    context: dict[str, Any] | None,
    prompt: str = "",
) -> bool:
    """Return whether an unclear reply should remain attached to this task.

    A task waiting for Oracle is deliberately excluded. Status questions or
    new work submitted while a job runs must be interpreted normally rather
    than being captured by the old request.
    """
    if not isinstance(context, dict):
        return False
    phase = str(context.get("phase") or "").upper()
    if phase in _FOCUSED_REPLY_PHASES:
        return True
    if phase != "READY_FOR_PLAN":
        return False
    words = " ".join(str(prompt or "").casefold().split()).split()
    if not words or len(words) > 7:
        return False
    allowed = {
        "yes", "yeah", "yep", "ok", "okay", "sure", "please", "prepare",
        "run", "execute", "start", "it", "that", "one", "do", "go",
        "ahead", "continue", "proceed", "now",
    }
    actionable = {
        "yes", "yeah", "yep", "ok", "okay", "sure", "prepare", "run",
        "execute", "start", "do", "continue", "proceed",
    }
    return all(word in allowed for word in words) and any(
        word in actionable for word in words
    )


def phase_for_interrupt(kind: str) -> str | None:
    """Map one durable LangGraph interrupt to its task lifecycle phase."""
    normalized = str(kind or "").strip().casefold()
    if normalized == "governed_operation_preparation":
        return "AWAITING_APPROVAL"
    if normalized in {
        "operation_artifact_selection",
        "operation_input_collection",
    }:
        return "AWAITING_USER_INPUT"
    return None


def reconcile_task_context(
    context: dict[str, Any] | None,
    decisions: Sequence[AgentActionDecision],
) -> dict[str, Any] | None:
    """Reduce durable approval/execution facts into one task context.

    New decisions include the originating task ID in their audited snapshot.
    Older decisions remain valid audit records but cannot safely transition a
    task because their relationship to a checkpoint cannot be proven.
    Regardless of age, the latest decision supplies bounded operational
    references that help interpret status and repeat requests.
    """
    if not isinstance(context, dict):
        return None
    reconciled = dict(context)
    ordered = tuple(decisions)
    if not ordered:
        return reconciled

    latest = ordered[-1]
    _apply_operational_memory(reconciled, latest)

    task_id = str(reconciled.get("task_id") or "").strip()
    matching = tuple(
        decision
        for decision in ordered
        if task_id and _decision_task_id(decision) == task_id
    )
    if not matching:
        return reconciled

    decision = matching[-1]
    phase = _phase_for_decision(decision)
    reconciled["phase"] = phase
    reconciled["current_execution"] = _execution_reference(decision)
    reconciled["previous_action"] = _action_reference(
        decision,
        task_context=reconciled,
    )
    _apply_operational_memory(reconciled, decision)

    if phase in _TERMINAL_PHASES or phase == "WAITING_FOR_ORACLE":
        reconciled["missing_parameters"] = []
        reconciled["pending_slot"] = None
        reconciled["pending_interaction"] = None
    return reconciled


def _decision_task_id(decision: AgentActionDecision) -> str:
    snapshot = decision.payload_snapshot
    return (
        str(snapshot.get("task_id") or "").strip()
        if isinstance(snapshot, dict)
        else ""
    )


def _phase_for_decision(decision: AgentActionDecision) -> str:
    if decision.outcome_status == "PROCESSING":
        return "AWAITING_APPROVAL"
    if decision.outcome_status == "REJECTED":
        return "CANCELLED"
    if decision.outcome_status == "FAILED":
        return "FAILED"
    if decision.outcome_status == "APPROVED":
        return "COMPLETED"
    if decision.outcome_status != "SUBMITTED":
        return "READY_FOR_PLAN"
    completion = str(decision.completion_status or "").upper()
    if completion == "SUCCESS":
        return "COMPLETED"
    if completion in {"FAILED", "RECOVERY_REQUIRED"}:
        return "FAILED"
    if completion == "CANCELLED":
        return "CANCELLED"
    return "WAITING_FOR_ORACLE"


def _action_reference(
    decision: AgentActionDecision,
    *,
    task_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reference: dict[str, Any] = {
        "task_id": _decision_task_id(decision) or None,
        "operation_code": decision.operation_code,
        "artifact_name": decision.artifact_name,
        "decision": decision.decision,
        "outcome_status": decision.outcome_status,
        "execution_id": decision.execution_id,
        "completion_status": decision.completion_status,
    }
    # Preserve only structured task semantics. Secrets and uploaded file
    # contents are never copied into conversational memory.
    if isinstance(task_context, dict):
        reference.update(
            {
                "task_intent": str(task_context.get("intent") or "UNKNOWN"),
                "canonical_capability": str(
                    task_context.get("canonical_capability") or "unknown"
                ),
                "objective": str(task_context.get("objective") or "")[:1_000],
            }
        )
        parameters = task_context.get("parameters")
        if isinstance(parameters, dict):
            reference["parameters"] = dict(parameters)
    return reference


def _execution_reference(decision: AgentActionDecision) -> dict[str, Any] | None:
    if not decision.execution_id or decision.execution_id.startswith("schedule:"):
        return None
    return {
        "execution_id": decision.execution_id,
        "operation_code": decision.operation_code,
        "artifact_name": decision.artifact_name,
        "status": decision.completion_status or decision.outcome_status,
    }


def _apply_operational_memory(
    context: dict[str, Any],
    decision: AgentActionDecision,
) -> None:
    references = context.get("recent_references")
    recent = dict(references) if isinstance(references, dict) else {}
    recent.update(
        {
            "last_operation_code": decision.operation_code,
            "last_artifact_name": decision.artifact_name or "",
            "last_execution_id": decision.execution_id or "",
            "last_execution_status": (
                decision.completion_status or decision.outcome_status
            ),
        }
    )
    context["recent_references"] = recent
