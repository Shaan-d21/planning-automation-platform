"""Stable contract implemented by model-provider adapters."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

from app.agent.models import (
    AgentMessage,
    AgentProviderResult,
    AgentProviderTurn,
    AgentToolActivity,
    AgentToolCall,
    AgentToolDefinition,
)


AgentToolExecutor = Callable[[AgentToolCall], dict[str, Any]]


class AgentProvider(Protocol):
    """Generate an answer and optionally call governed platform tools."""

    @property
    def provider_name(self) -> str:
        """Return the stable provider identifier."""

    @property
    def model_name(self) -> str:
        """Return the configured model identifier."""

    def respond(
        self,
        *,
        messages: Sequence[AgentMessage],
        system_instruction: str,
        tools: Sequence[AgentToolDefinition],
        execute_tool: AgentToolExecutor,
    ) -> AgentProviderResult:
        """Return a provider-neutral response for one conversation turn."""


class StepAgentProvider(Protocol):
    """Provider adapter used by LangGraph one model step at a time."""

    @property
    def provider_name(self) -> str:
        """Return the stable provider identifier."""

    @property
    def model_name(self) -> str:
        """Return the configured model identifier."""

    def generate(
        self,
        *,
        messages: Sequence[AgentMessage],
        system_instruction: str,
        tools: Sequence[AgentToolDefinition],
        provider_exchange: Sequence[dict[str, Any]],
        required_tool_name: str | None = None,
    ) -> AgentProviderTurn:
        """Generate exactly one model response without executing tools."""

    def tool_response(
        self,
        *,
        calls: Sequence[AgentToolCall],
        activities: Sequence[AgentToolActivity],
    ) -> dict[str, Any]:
        """Build one provider-native function-result turn."""
