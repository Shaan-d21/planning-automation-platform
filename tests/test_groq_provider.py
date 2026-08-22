"""Tests for the Groq provider adapter and local tool-call correlation."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.agent.groq_provider import GroqAgentProvider
from app.agent.models import (
    AgentMessage,
    AgentMessageRole,
    AgentToolActivity,
    AgentToolDefinition,
)
from app.utils.exceptions import AgentProviderError


class _Completions:
    def __init__(self, responses) -> None:
        self._responses = iter(responses)
        self.calls: list[dict[str, object]] = []

    def create(self, **values):
        self.calls.append(values)
        return next(self._responses)


def _response(*, content=None, tool_calls=()):
    return SimpleNamespace(
        choices=(
            SimpleNamespace(
                message=SimpleNamespace(
                    content=content,
                    tool_calls=tool_calls,
                )
            ),
        )
    )


def _call():
    return SimpleNamespace(
        id="call-123",
        function=SimpleNamespace(
            name="get_environment_summary",
            arguments='{"scope":"current"}',
        ),
    )


def test_groq_step_adapter_preserves_tool_call_and_result_ids() -> None:
    completions = _Completions(
        (
            _response(tool_calls=(_call(),)),
            _response(content="The current environment is Vision."),
        )
    )
    provider = GroqAgentProvider(
        api_key="test-key",
        model="openai/gpt-oss-120b",
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        ),
    )
    messages = (
        AgentMessage(
            message_id=1,
            conversation_id="conversation-1",
            role=AgentMessageRole.USER,
            content="Which environment is configured?",
            created_at=datetime.now(UTC),
        ),
    )
    tools = (
        AgentToolDefinition(
            name="get_environment_summary",
            description="Return the environment.",
            parameters_schema={"type": "object", "properties": {}},
        ),
    )

    first = provider.generate(
        messages=messages,
        system_instruction="Read only.",
        tools=tools,
        provider_exchange=(),
    )
    activity = AgentToolActivity(
        name=first.tool_calls[0].name,
        arguments=first.tool_calls[0].arguments,
        status="SUCCESS",
        summary="Environment returned.",
        result={"application": "Vision"},
    )
    tool_result = provider.tool_response(
        calls=first.tool_calls,
        activities=(activity,),
    )
    second = provider.generate(
        messages=messages,
        system_instruction="Read only.",
        tools=tools,
        provider_exchange=(first.provider_content, tool_result),
    )

    assert first.tool_calls[0].call_id == "call-123"
    assert first.tool_calls[0].arguments == {"scope": "current"}
    assert second.text == "The current environment is Vision."
    continued_messages = completions.calls[1]["messages"]
    assert continued_messages[-2]["tool_calls"][0]["id"] == "call-123"
    assert continued_messages[-1]["role"] == "tool"
    assert continued_messages[-1]["tool_call_id"] == "call-123"


def test_groq_tool_schema_uses_official_function_shape() -> None:
    definition = AgentToolDefinition(
        name="list_planning_cubes",
        description="List cubes.",
        parameters_schema={"type": "object", "properties": {}},
    )

    payload = GroqAgentProvider._tool(definition)

    assert payload == {
        "type": "function",
        "function": {
            "name": "list_planning_cubes",
            "description": "List cubes.",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_groq_compacts_old_history_and_caps_completion() -> None:
    completions = _Completions((_response(content="Ready."),))
    provider = GroqAgentProvider(
        api_key="test-key",
        model="openai/gpt-oss-120b",
        max_input_tokens=1_000,
        max_completion_tokens=256,
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    messages = tuple(
        AgentMessage(
            message_id=index,
            conversation_id="conversation-long",
            role=(
                AgentMessageRole.USER
                if index % 2
                else AgentMessageRole.ASSISTANT
            ),
            content=(f"message-{index} " + "context " * 180),
            created_at=datetime.now(UTC),
        )
        for index in range(1, 11)
    )

    provider.generate(
        messages=messages,
        system_instruction="Keep the latest request.",
        tools=(),
        provider_exchange=(),
    )

    call = completions.calls[0]
    sent = call["messages"]
    assert len(sent) < len(messages) + 1
    assert "message-10" in sent[-1]["content"]
    assert call["max_completion_tokens"] == 256
    assert call["reasoning_effort"] == "low"
    assert call["include_reasoning"] is False


def test_groq_reports_token_limit_separately_from_api_key_failure() -> None:
    class _FailingCompletions:
        def create(self, **_values):
            raise RuntimeError(
                "Error code: 413 request too large; rate_limit_exceeded"
            )

    provider = GroqAgentProvider(
        api_key="valid-key",
        model="openai/gpt-oss-120b",
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=_FailingCompletions())
        ),
    )

    try:
        provider.generate(
            messages=(),
            system_instruction="Read only.",
            tools=(),
            provider_exchange=(),
        )
    except AgentProviderError as exc:
        assert "tokens-per-minute allowance is temporarily full" in str(exc)
        assert "API key is valid" in str(exc)
    else:  # pragma: no cover - assertion guard.
        raise AssertionError("Expected a token-limit error.")
