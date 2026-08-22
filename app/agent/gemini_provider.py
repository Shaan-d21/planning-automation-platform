"""Google Gemini implementation of the provider-neutral agent contract."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from app.agent.models import (
    AgentMessage,
    AgentMessageRole,
    AgentProviderResult,
    AgentProviderTurn,
    AgentToolActivity,
    AgentToolCall,
    AgentToolDefinition,
)
from app.agent.provider import AgentToolExecutor
from app.utils.exceptions import AgentConfigurationError, AgentProviderError


class GeminiAgentProvider:
    """Use Google Gen AI with explicit, application-controlled tool calls."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tool_rounds: int = 4,
        client: Any | None = None,
        types_module: Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if not api_key.strip():
            raise AgentConfigurationError(
                "GEMINI_API_KEY is not configured. Add a Google AI Studio "
                "API key to the environment and restart the application."
            )
        self._model = model.strip()
        self._max_tool_rounds = max_tool_rounds
        self._logger = logger or logging.getLogger(__name__)
        if client is not None and types_module is not None:
            self._client = client
            self._types = types_module
            return
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise AgentConfigurationError(
                "The Google Gen AI SDK is not installed. Run "
                "'pip install -r requirements.txt' and restart the app."
            ) from exc
        self._client = genai.Client(api_key=api_key)
        self._types = types

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model

    def respond(
        self,
        *,
        messages: Sequence[AgentMessage],
        system_instruction: str,
        tools: Sequence[AgentToolDefinition],
        execute_tool: AgentToolExecutor,
    ) -> AgentProviderResult:
        """Generate a response while retaining control over every tool call."""
        activity: list[AgentToolActivity] = []
        provider_exchange: list[dict[str, Any]] = []
        try:
            for _ in range(self._max_tool_rounds + 1):
                turn = self.generate(
                    messages=messages,
                    system_instruction=system_instruction,
                    tools=tools,
                    provider_exchange=provider_exchange,
                )
                calls = turn.tool_calls
                if not calls:
                    text = str(turn.text or "").strip()
                    if not text:
                        raise AgentProviderError(
                            "Gemini returned no answer. Rephrase the request "
                            "or try again."
                        )
                    return AgentProviderResult(text=text, tool_activity=tuple(activity))

                if turn.provider_content is None:
                    raise AgentProviderError(
                        "Gemini omitted the model tool-call content required "
                        "to continue the conversation safely."
                    )
                provider_exchange.append(turn.provider_content)
                round_activity: list[AgentToolActivity] = []
                for call in calls:
                    try:
                        result = execute_tool(call)
                    except Exception as exc:  # Tool failure is returned to the model.
                        self._logger.warning(
                            "Agent tool '%s' failed: %s", call.name, exc
                        )
                        result = {"error": str(exc)}
                        status = "FAILED"
                    else:
                        status = "SUCCESS"
                    round_activity.append(
                        AgentToolActivity(
                            name=str(call.name),
                            arguments=call.arguments,
                            status=status,
                            summary=self._tool_summary(result),
                            result=result,
                        )
                    )
                activity.extend(round_activity)
                provider_exchange.append(
                    self.tool_response(calls=calls, activities=round_activity)
                )
        except (AgentProviderError, AgentConfigurationError):
            raise
        except Exception as exc:
            self._logger.exception("Gemini request failed.")
            raise AgentProviderError(
                "Google Gemini could not complete the request. Verify the API "
                "key, model availability, quota, and network connection."
            ) from exc
        raise AgentProviderError(
            "The agent reached its safe tool-call limit. Narrow the request "
            "and try again."
        )

    def generate(
        self,
        *,
        messages: Sequence[AgentMessage],
        system_instruction: str,
        tools: Sequence[AgentToolDefinition],
        provider_exchange: Sequence[dict[str, Any]],
    ) -> AgentProviderTurn:
        """Generate one Gemini turn; application code owns tool execution."""
        try:
            contents = [self._content(message) for message in messages]
            contents.extend(
                self._deserialize_content(item) for item in provider_exchange
            )
            declarations = [self._function_declaration(tool) for tool in tools]
            config = self._types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.2,
                tools=[self._types.Tool(function_declarations=declarations)],
                automatic_function_calling=(
                    self._types.AutomaticFunctionCallingConfig(disable=True)
                ),
            )
            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )
            calls = tuple(
                AgentToolCall(
                    name=str(call.name),
                    arguments=dict(call.args or {}),
                    call_id=(
                        str(call.id) if getattr(call, "id", None) else None
                    ),
                )
                for call in tuple(response.function_calls or ())
            )
            content = None
            if calls:
                content = self._serialize_content(
                    response.candidates[0].content
                )
            return AgentProviderTurn(
                text=str(response.text or "").strip() or None,
                tool_calls=calls,
                provider_content=content,
            )
        except (AgentProviderError, AgentConfigurationError):
            raise
        except Exception as exc:
            self._logger.exception("Gemini request failed.")
            raise AgentProviderError(
                "Google Gemini could not complete the request. Verify the API "
                "key, model availability, quota, and network connection."
            ) from exc

    def tool_response(
        self,
        *,
        calls: Sequence[AgentToolCall],
        activities: Sequence[AgentToolActivity],
    ) -> dict[str, Any]:
        """Create Gemini's correlated user-role function-response turn."""
        if len(calls) != len(activities):
            raise AgentProviderError(
                "Tool calls and tool results could not be correlated."
            )
        parts = [
            self._function_response_part(call, activity.result or {})
            for call, activity in zip(calls, activities, strict=True)
        ]
        return self._serialize_content(
            self._types.Content(role="user", parts=parts)
        )

    def _content(self, message: AgentMessage):
        role = "model" if message.role is AgentMessageRole.ASSISTANT else "user"
        return self._types.Content(
            role=role,
            parts=[self._types.Part.from_text(text=message.content)],
        )

    def _function_declaration(
        self,
        tool: AgentToolDefinition,
    ):
        """Build a declaration with the current Google Gen AI SDK."""
        return self._types.FunctionDeclaration(
            name=tool.name,
            description=tool.description,
            parameters_json_schema=tool.parameters_schema,
        )

    def _function_response_part(self, call: Any, result: dict[str, Any]):
        """Return a correlated function result for the current Gemini turn."""
        return self._types.Part(
            function_response=self._types.FunctionResponse(
                id=(
                    str(getattr(call, "call_id", None) or getattr(call, "id", ""))
                    or None
                ),
                name=str(call.name),
                response={"result": result},
            )
        )

    @staticmethod
    def _serialize_content(content: Any) -> dict[str, Any]:
        """Serialize complete Gemini content, including thought signatures."""
        model_dump = getattr(content, "model_dump", None)
        if callable(model_dump):
            return dict(model_dump(mode="json", exclude_none=True))
        return {
            "role": str(getattr(content, "role", "user")),
            "parts": list(getattr(content, "parts", ())),
        }

    def _deserialize_content(self, payload: dict[str, Any]):
        """Restore one checkpoint-safe Gemini content object."""
        validator = getattr(self._types.Content, "model_validate", None)
        if callable(validator):
            return validator(payload)
        return self._types.Content(**payload)

    @staticmethod
    def _tool_summary(result: dict[str, Any]) -> str:
        if "error" in result:
            return str(result["error"])[:240]
        if "action_draft" in result:
            return "Action draft prepared; no Oracle action was executed."
        count = result.get("count")
        if isinstance(count, int):
            return f"Returned {count} item{'s' if count != 1 else ''}."
        return "Read-only platform information returned."
