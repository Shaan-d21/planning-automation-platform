"""Tests for credential-free agent decision and lifecycle telemetry."""

from __future__ import annotations

import json
import logging

from app.agent.observability import log_agent_transition


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _logger() -> tuple[logging.Logger, _CaptureHandler]:
    logger = logging.getLogger("test.agent.transition")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    handler = _CaptureHandler()
    logger.addHandler(handler)
    return logger, handler


def test_transition_event_records_state_facts_without_parameter_values() -> None:
    logger, handler = _logger()

    emitted = log_agent_transition(
        logger,
        conversation_id="conversation-1",
        user_id=17,
        previous_context={
            "task_id": "task-1",
            "phase": "COLLECTING_INFORMATION",
            "canonical_capability": "data.import",
            "parameters": {"year": "FY27", "password": "old-secret"},
        },
        current_context={
            "task_id": "task-1",
            "phase": "AWAITING_USER_INPUT",
            "canonical_capability": "data.import",
            "action_mode": "execute",
            "parameters": {"year": "FY28", "password": "new-secret"},
            "missing_parameters": ["file"],
            "parameter_conflicts": [
                {"name": "year", "values": ["FY27", "FY28"]}
            ],
            "resolved_entity": {
                "status": "ambiguous",
                "candidates": ["Import Actuals", "Import Forecast"],
            },
            "pending_interaction": {"kind": "operation_input_collection"},
        },
        trigger="graph_result",
        turn_id="turn-1",
    )

    assert emitted is True
    assert len(handler.messages) == 1
    payload = json.loads(handler.messages[0].split(": ", 1)[1])
    assert payload["from_phase"] == "COLLECTING_INFORMATION"
    assert payload["to_phase"] == "AWAITING_USER_INPUT"
    assert payload["changed_parameters"] == ["password", "year"]
    assert payload["conflict_slots"] == ["year"]
    assert payload["entity_candidate_count"] == 2
    assert payload["pending_interaction"] == "operation_input_collection"
    serialized = json.dumps(payload)
    assert "FY27" not in serialized
    assert "FY28" not in serialized
    assert "old-secret" not in serialized
    assert "new-secret" not in serialized
    assert "Import Actuals" not in serialized


def test_unchanged_task_state_does_not_emit_transition_noise() -> None:
    logger, handler = _logger()
    context = {
        "task_id": "task-1",
        "phase": "READY_FOR_PLAN",
        "canonical_capability": "business_rule.run",
        "parameters": {"year": "FY27"},
    }

    emitted = log_agent_transition(
        logger,
        conversation_id="conversation-1",
        user_id=17,
        previous_context=context,
        current_context=dict(context),
        trigger="graph_result",
    )

    assert emitted is False
    assert handler.messages == []
