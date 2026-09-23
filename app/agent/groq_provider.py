"""Groq implementation of the provider-neutral LangGraph contract."""

from __future__ import annotations

import json
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


class GroqAgentProvider:
    """Use Groq Chat Completions with application-controlled local tools."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tool_rounds: int = 4,
        max_input_tokens: int = 2_500,
        max_completion_tokens: int = 384,
        timeout: float = 30.0,
        client: Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if not api_key.strip():
            raise AgentConfigurationError(
                "GROQ_API_KEY is not configured. Add a GroqCloud API key "
                "to the environment and restart the application."
            )
        self._model = model.strip()
        self._max_tool_rounds = max_tool_rounds
        self._max_input_tokens = max_input_tokens
        self._max_completion_tokens = max_completion_tokens
        self._logger = logger or logging.getLogger(__name__)
        if client is not None:
            self._client = client
            return
        try:
            from groq import Groq
        except ImportError as exc:
            raise AgentConfigurationError(
                "The Groq SDK is not installed. Run "
                "'pip install -r requirements.txt' and restart the app."
            ) from exc
        self._client = Groq(
            api_key=api_key,
            timeout=timeout,
            max_retries=2,
        )

    @property
    def provider_name(self) -> str:
        return "groq"

    @property
    def model_name(self) -> str:
        return self._model

    def generate(
        self,
        *,
        messages: Sequence[AgentMessage],
        system_instruction: str,
        tools: Sequence[AgentToolDefinition],
        provider_exchange: Sequence[dict[str, Any]],
        required_tool_name: str | None = None,
    ) -> AgentProviderTurn:
        """Generate exactly one Groq model turn without executing tools."""
        tool_payloads = [self._tool(item) for item in tools]
        history = [self._message(item) for item in messages]
        exchange: list[dict[str, Any]] = []
        for item in provider_exchange:
            if item.get("_provider") != "groq":
                raise AgentProviderError(
                    "The saved model exchange does not belong to Groq."
                )
            exchange_messages = item.get("messages")
            if not isinstance(exchange_messages, list):
                raise AgentProviderError("The saved Groq exchange is invalid.")
            exchange.extend(dict(message) for message in exchange_messages)
        history = self._compact_history(
            history,
            system_instruction=system_instruction,
            tools=tool_payloads,
            provider_exchange=exchange,
        )
        request_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_instruction},
            *history,
            *exchange,
        ]
        estimated_input_tokens = self._estimate_tokens(
            {"messages": request_messages, "tools": tool_payloads}
        )
        self._logger.info(
            "Groq request prepared: model='%s', estimated_input_tokens=%d, "
            "completion_limit=%d, tools=%d.",
            self._model,
            estimated_input_tokens,
            self._max_completion_tokens,
            len(tool_payloads),
        )
        request_options: dict[str, Any] = {}
        if self._model.casefold().startswith("openai/gpt-oss-"):
            # Groq supports low reasoning effort for GPT-OSS. The assistant's
            # deterministic Python graph owns validation and governance, so a
            # larger hidden reasoning budget adds cost without adding safety.
            request_options.update(
                reasoning_effort="low",
                include_reasoning=False,
            )
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=request_messages,
                tools=tool_payloads,
                tool_choice=(
                    {
                        "type": "function",
                        "function": {"name": required_tool_name},
                    }
                    if required_tool_name else "auto"
                ),
                parallel_tool_calls=not bool(required_tool_name),
                temperature=0.2,
                max_completion_tokens=self._max_completion_tokens,
                **request_options,
            )
            message = response.choices[0].message
            raw_calls = tuple(message.tool_calls or ())
            calls = tuple(self._tool_call(item) for item in raw_calls)
            provider_content = None
            if calls:
                provider_content = {
                    "_provider": "groq",
                    "messages": [self._assistant_tool_message(message, raw_calls)],
                }
            text = str(message.content or "").strip() or None
            return AgentProviderTurn(
                text=text,
                tool_calls=calls,
                provider_content=provider_content,
            )
        except (AgentProviderError, AgentConfigurationError):
            raise
        except Exception as exc:
            self._logger.exception("Groq request failed.")
            raise AgentProviderError(self._error_message(exc)) from exc

    def _compact_history(
        self,
        messages: list[dict[str, Any]],
        *,
        system_instruction: str,
        tools: list[dict[str, Any]],
        provider_exchange: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Retain recent history within the configured Groq token envelope."""
        fixed_tokens = self._estimate_tokens(
            {
                "system": system_instruction,
                "tools": tools,
                "provider_exchange": provider_exchange,
            }
        )
        if fixed_tokens >= self._max_input_tokens:
            self._logger.warning(
                "Groq fixed request context uses %d estimated tokens against "
                "a %d-token input budget.",
                fixed_tokens,
                self._max_input_tokens,
            )
        available = max(192, self._max_input_tokens - fixed_tokens)
        selected: list[dict[str, Any]] = []
        used = 0
        for message in reversed(messages):
            cost = self._estimate_tokens(message)
            if selected and used + cost > available:
                break
            if not selected and cost > available:
                content = str(message.get("content") or "")
                message = {
                    **message,
                    "content": content[-max(256, available * 2 - 128) :],
                }
                cost = self._estimate_tokens(message)
            selected.append(message)
            used += cost
        selected.reverse()
        dropped = len(messages) - len(selected)
        if dropped:
            self._logger.info(
                "Compacted Groq conversation history: dropped %d old "
                "message(s), retained %d.",
                dropped,
                len(selected),
            )
        return selected

    @staticmethod
    def _estimate_tokens(value: Any) -> int:
        """Conservatively estimate chat tokens without a model tokenizer."""
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        # JSON tool schemas contain more punctuation than natural prose and
        # therefore tokenize less efficiently. Two UTF-8 bytes per token is
        # deliberately conservative and keeps multi-round Groq requests well
        # below free-tier TPM ceilings.
        return max(1, (len(serialized.encode("utf-8")) + 1) // 2)

    @staticmethod
    def _error_message(error: Exception) -> str:
        details = str(error).casefold()
        if (
            "tokens per minute" in details
            or " tpm" in details
            or "rate_limit_exceeded" in details
        ):
            return (
                "Groq's tokens-per-minute allowance is temporarily full. "
                "Wait about one minute and try again. The API key is valid; "
                "a different key has the same limit when it belongs to the "
                "same free service tier."
            )
        if "413" in details or "request too large" in details:
            return (
                "The Groq request is larger than the configured context "
                "limit. Start a new conversation or reduce the message size."
            )
        if "429" in details or "rate_limit_exceeded" in details:
            return (
                "The Groq organization token limit was reached. Wait for the "
                "rate-limit window to reset, then try again."
            )
        if "401" in details or "invalid api key" in details:
            return (
                "Groq rejected GROQ_API_KEY. Verify the key and restart the "
                "application."
            )
        return (
            "Groq could not complete the request. Verify the selected model, "
            "account availability, and network connection."
        )

    def tool_response(
        self,
        *,
        calls: Sequence[AgentToolCall],
        activities: Sequence[AgentToolActivity],
    ) -> dict[str, Any]:
        """Build the correlated Groq tool-result messages for one round."""
        if len(calls) != len(activities):
            raise AgentProviderError(
                "Tool calls and Groq tool results could not be correlated."
            )
        messages: list[dict[str, Any]] = []
        for call, activity in zip(calls, activities, strict=True):
            if not call.call_id:
                raise AgentProviderError(
                    f"Groq tool call '{call.name}' did not include a call ID."
                )
            payload = activity.result or {
                "status": activity.status,
                "message": activity.summary,
            }
            payload = self._compact_tool_result(payload)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.call_id,
                    "name": call.name,
                    "content": json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ),
                }
            )
        return {"_provider": "groq", "messages": messages}

    def _compact_tool_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Limit catalog duplication sent back to Groq after a tool call."""
        maximum_tokens = max(384, self._max_input_tokens // 4)
        if self._estimate_tokens(payload) <= maximum_tokens:
            return payload
        compact = {
            key: value
            for key, value in payload.items()
            if key not in {"artifacts", "artifact_details", "items"}
        }
        for key in ("artifacts", "artifact_details", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                compact[key] = value[:25]
        compact["truncated_for_model"] = True
        compact["message"] = (
            "The model received a preview only; the platform retains the full "
            "live catalog for deterministic user selection."
        )
        return compact

    def respond(
        self,
        *,
        messages: Sequence[AgentMessage],
        system_instruction: str,
        tools: Sequence[AgentToolDefinition],
        execute_tool: AgentToolExecutor,
    ) -> AgentProviderResult:
        """Compatibility loop used only by the temporary legacy switch."""
        exchange: list[dict[str, Any]] = []
        activity: list[AgentToolActivity] = []
        for _ in range(self._max_tool_rounds + 1):
            turn = self.generate(
                messages=messages,
                system_instruction=system_instruction,
                tools=tools,
                provider_exchange=exchange,
            )
            if not turn.tool_calls:
                text = str(turn.text or "").strip()
                if not text:
                    raise AgentProviderError(
                        "Groq returned no answer. Rephrase the request and try again."
                    )
                return AgentProviderResult(text=text, tool_activity=tuple(activity))
            if turn.provider_content is None:
                raise AgentProviderError(
                    "Groq omitted the tool-call content required to continue."
                )
            exchange.append(turn.provider_content)
            round_activity: list[AgentToolActivity] = []
            for call in turn.tool_calls:
                try:
                    result = execute_tool(call)
                except Exception as exc:
                    self._logger.warning(
                        "Agent tool '%s' failed: %s", call.name, exc
                    )
                    result = {"error": str(exc)}
                    status = "FAILED"
                else:
                    status = "SUCCESS"
                round_activity.append(
                    AgentToolActivity(
                        name=call.name,
                        arguments=call.arguments,
                        status=status,
                        summary=self._tool_summary(result),
                        result=result,
                    )
                )
            activity.extend(round_activity)
            exchange.append(
                self.tool_response(
                    calls=turn.tool_calls,
                    activities=round_activity,
                )
            )
        raise AgentProviderError(
            "The agent reached its safe tool-call limit. Narrow the request "
            "and try again."
        )

    @staticmethod
    def _message(message: AgentMessage) -> dict[str, str]:
        role = (
            "assistant"
            if message.role is AgentMessageRole.ASSISTANT
            else "user"
        )
        return {"role": role, "content": message.content}

    @staticmethod
    def _tool(tool: AgentToolDefinition) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters_schema,
            },
        }

    @staticmethod
    def _tool_call(call: Any) -> AgentToolCall:
        raw_arguments = call.function.arguments or "{}"
        try:
            arguments = json.loads(raw_arguments)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AgentProviderError(
                f"Groq returned invalid JSON arguments for '{call.function.name}'."
            ) from exc
        if not isinstance(arguments, dict):
            raise AgentProviderError(
                f"Groq returned non-object arguments for '{call.function.name}'."
            )
        return AgentToolCall(
            name=str(call.function.name),
            arguments=arguments,
            call_id=str(call.id),
        )

    @staticmethod
    def _assistant_tool_message(
        message: Any,
        calls: Sequence[Any],
    ) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": str(call.id),
                    "type": "function",
                    "function": {
                        "name": str(call.function.name),
                        "arguments": str(call.function.arguments or "{}"),
                    },
                }
                for call in calls
            ],
        }

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
