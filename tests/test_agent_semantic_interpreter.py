from __future__ import annotations

from datetime import UTC, datetime

from app.agent.canonical import CanonicalCapability
from app.agent.models import (
    AgentMessage,
    AgentMessageRole,
    AgentProviderTurn,
    AgentToolCall,
)
from app.agent.semantic_interpreter import (
    AgentSemanticInterpreter,
    semantic_task_understanding,
)
from app.agent.task_state import AgentTaskIntent, AgentTaskPhase


def _message(content: str) -> AgentMessage:
    return AgentMessage(
        message_id=1,
        conversation_id="conversation-1",
        role=AgentMessageRole.USER,
        content=content,
        created_at=datetime.now(UTC),
    )


class StructuredProvider:
    def __init__(self, arguments):
        self.arguments = arguments
        self.tools = ()

    def generate(
        self,
        *,
        messages,
        system_instruction,
        tools,
        provider_exchange,
        required_tool_name=None,
    ):
        self.tools = tools
        assert required_tool_name == "record_task_interpretation"
        return AgentProviderTurn(
            tool_calls=(
                AgentToolCall(
                    name="record_task_interpretation",
                    arguments=self.arguments,
                ),
            )
        )


def test_semantic_interpreter_supports_hinglish_without_execution_payload() -> None:
    provider = StructuredProvider(
        {
            "capability": "business_rule.run",
            "action_mode": "execute",
            "execution_requested": True,
            "entity_type": "business_rule",
            "entity_name": "Actual to Forecast",
            "parameters": {"year": "FY27"},
            "confidence": "high",
            "objective": "FY27 ke liye Actual to Forecast rule chalao",
        }
    )

    result = AgentSemanticInterpreter.interpret(
        provider=provider,
        messages=(_message("FY27 ke liye Actual to Forecast rule chalao"),),
        prior_context=None,
    )

    assert result is not None
    assert result.capability is CanonicalCapability.BUSINESS_RULE
    assert result.entity_name == "Actual to Forecast"
    assert len(provider.tools) == 1
    assert "payload" not in provider.tools[0].parameters_schema.get(
        "properties", {}
    )


def test_semantic_interpreter_rejects_invalid_extra_model_output() -> None:
    provider = StructuredProvider(
        {
            "capability": "business_rule.run",
            "action_mode": "execute",
            "execution_requested": True,
            "oracle_payload": {"jobType": "RULES"},
        }
    )

    assert AgentSemanticInterpreter.interpret(
        provider=provider,
        messages=(_message("rule chalao"),),
        prior_context=None,
    ) is None


def test_negated_semantics_never_become_an_executable_legacy_task() -> None:
    provider = StructuredProvider(
        {
            "capability": "business_rule.run",
            "action_mode": "explain",
            "execution_requested": False,
            "negated": True,
            "confidence": "high",
            "objective": "Explain Aggregate Plan but do not run it.",
        }
    )
    parsed = AgentSemanticInterpreter.interpret(
        provider=provider,
        messages=(_message("Explain Aggregate Plan but don't run it"),),
        prior_context=None,
    )
    assert parsed is not None

    task = semantic_task_understanding(parsed, fallback_objective="fallback")

    assert task.intent is AgentTaskIntent.HELP_EXPLAIN
    assert task.phase is AgentTaskPhase.UNDERSTANDING_REQUEST
