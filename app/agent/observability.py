"""Structured, credential-free decision events for agent troubleshooting."""

from __future__ import annotations

import json
import logging
from typing import Any


def _bounded_text(value: object, *, limit: int = 120) -> str:
    return " ".join(str(value or "").split())[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def log_agent_decision(
    logger: logging.Logger,
    *,
    conversation_id: str,
    user_id: int,
    task_context: dict[str, Any],
    routed_intent: str,
    allowed_tools: tuple[str, ...] | list[str] | frozenset[str],
) -> None:
    """Emit one bounded JSON decision event with no prompt or credentials."""
    entity = task_context.get("resolved_entity")
    entity_payload = entity if isinstance(entity, dict) else {}
    sources = task_context.get("parameter_sources")
    parameter_sources = sources if isinstance(sources, dict) else {}
    event = {
        "event": "agent_decision",
        "conversation_id": conversation_id,
        "user_id": user_id,
        "task_id": str(task_context.get("task_id") or ""),
        "legacy_intent": str(task_context.get("intent") or "UNKNOWN"),
        "canonical_capability": str(
            task_context.get("canonical_capability") or "unknown"
        ),
        "action_mode": str(task_context.get("action_mode") or "unknown"),
        "phase": str(task_context.get("phase") or ""),
        "confidence": str(task_context.get("confidence") or ""),
        "routed_intent": routed_intent,
        "missing_parameters": list(
            task_context.get("missing_parameters") or ()
        )[:20],
        "parameter_sources": parameter_sources,
        "entity_status": str(entity_payload.get("status") or "unresolved"),
        "entity_type": str(entity_payload.get("entity_type") or ""),
        "allowed_tools": sorted(str(item) for item in allowed_tools),
    }
    logger.info(
        "Agent decision event: %s",
        json.dumps(event, ensure_ascii=True, separators=(",", ":")),
    )


def log_agent_transition(
    logger: logging.Logger,
    *,
    conversation_id: str,
    user_id: int,
    previous_context: dict[str, Any] | None,
    current_context: dict[str, Any] | None,
    trigger: str,
    turn_id: str | None = None,
    request_id: str | None = None,
    execution_id: str | None = None,
) -> bool:
    """Emit a value-free state transition event when meaningful state changed.

    Parameter values, prompts, Oracle payloads, and credentials are deliberately
    excluded. The event records only bounded identifiers, state names, changed
    slot names, and aggregate entity-resolution facts.
    """
    previous = _mapping(previous_context)
    current = _mapping(current_context)
    if not current:
        return False
    previous_parameters = _mapping(previous.get("parameters"))
    current_parameters = _mapping(current.get("parameters"))
    changed_parameters = sorted(
        name
        for name in set(previous_parameters) | set(current_parameters)
        if previous_parameters.get(name) != current_parameters.get(name)
    )[:30]
    previous_phase = _bounded_text(previous.get("phase")).upper()
    current_phase = _bounded_text(current.get("phase")).upper()
    previous_task_id = _bounded_text(previous.get("task_id"))
    current_task_id = _bounded_text(current.get("task_id"))
    previous_capability = _bounded_text(
        previous.get("canonical_capability") or "unknown"
    )
    current_capability = _bounded_text(
        current.get("canonical_capability") or "unknown"
    )
    if not any(
        (
            previous_task_id != current_task_id,
            previous_phase != current_phase,
            previous_capability != current_capability,
            bool(changed_parameters),
        )
    ):
        return False

    entity = _mapping(current.get("resolved_entity"))
    candidates = entity.get("candidates")
    conflicts = current.get("parameter_conflicts")
    conflict_names = sorted(
        {
            _bounded_text(item.get("name"), limit=80)
            for item in conflicts
            if isinstance(item, dict) and item.get("name")
        }
    )[:20] if isinstance(conflicts, list) else []
    pending = _mapping(current.get("pending_interaction"))
    current_execution = _mapping(current.get("current_execution"))
    event = {
        "event": "agent_task_transition",
        "trigger": _bounded_text(trigger, limit=80),
        "conversation_id": _bounded_text(conversation_id),
        "user_id": user_id,
        "task_id": current_task_id,
        "previous_task_id": previous_task_id,
        "from_phase": previous_phase,
        "to_phase": current_phase,
        "canonical_capability": current_capability,
        "action_mode": _bounded_text(current.get("action_mode") or "unknown"),
        "dialogue_act": _bounded_text(current.get("dialogue_act")),
        "changed_parameters": changed_parameters,
        "missing_parameters": sorted(
            _bounded_text(item, limit=80)
            for item in (current.get("missing_parameters") or ())
        )[:30],
        "conflict_slots": conflict_names,
        "entity_status": _bounded_text(entity.get("status") or "unresolved"),
        "entity_candidate_count": (
            len(candidates) if isinstance(candidates, list | tuple) else 0
        ),
        "pending_interaction": _bounded_text(pending.get("kind")),
        "turn_id": _bounded_text(turn_id),
        "request_id": _bounded_text(request_id),
        "execution_id": _bounded_text(
            execution_id or current_execution.get("execution_id")
        ),
    }
    logger.info(
        "Agent task transition event: %s",
        json.dumps(event, ensure_ascii=True, separators=(",", ":")),
    )
    return True
