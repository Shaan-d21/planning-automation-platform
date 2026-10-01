"""Structured, credential-free decision events for agent troubleshooting."""

from __future__ import annotations

import json
import logging
from typing import Any


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

