"""Domain models shared by agent providers and the application layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class AgentMessageRole(StrEnum):
    """Roles persisted in one agent conversation."""

    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class AgentMessage:
    """One persisted conversation message."""

    message_id: int
    conversation_id: str
    role: AgentMessageRole
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AgentConversation:
    """Conversation owned by one authenticated platform user."""

    conversation_id: str
    user_id: int
    title: str
    provider: str
    model: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AgentToolDefinition:
    """Provider-independent function declaration exposed to an LLM."""

    name: str
    description: str
    parameters_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AgentToolCall:
    """One provider-requested read-only capability call."""

    name: str
    arguments: dict[str, Any]
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class AgentProviderTurn:
    """One model step consumed by the application-owned agent graph."""

    text: str | None = None
    tool_calls: tuple[AgentToolCall, ...] = ()
    provider_content: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AgentToolActivity:
    """Auditable result of one capability invocation."""

    name: str
    arguments: dict[str, Any]
    status: str
    summary: str
    result: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AgentApprovalRequest:
    """Durable human decision requested by an interrupted agent graph."""

    request_id: str
    operation_code: str
    display_name: str
    objective: str
    artifact_name: str | None
    category: str
    risk_level: str
    route: str
    effect: str = "Prepare a governed handoff; no Oracle action will run."
    input_values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentClarificationRequest:
    """Durable structured choice required before action preparation."""

    request_id: str
    operation_code: str
    display_name: str
    prompt: str
    options: tuple[str, ...]
    allows_cancel: bool = True
    recommendations: tuple[dict[str, Any], ...] = ()
    option_labels: dict[str, str] = field(default_factory=dict)
    catalog_recovery: dict[str, Any] = field(default_factory=dict)
    search_context: str = ""


@dataclass(frozen=True, slots=True)
class AgentInputRequest:
    """Durable structured values required before preparation approval."""

    request_id: str
    operation_code: str
    display_name: str
    artifact_name: str
    title: str
    description: str
    fields: tuple[dict[str, Any], ...]
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentDraftCheck:
    """One deterministic check performed against an action draft."""

    code: str
    label: str
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class AgentActionDraft:
    """Persisted, non-executable handoff to a governed platform screen."""

    draft_id: str
    conversation_id: str
    message_id: int
    action_type: str
    target_code: str
    display_name: str
    category: str
    risk_level: str
    route: str
    objective: str
    artifact_name: str | None
    required_inputs: tuple[str, ...]
    stages: tuple[str, ...]
    approval_required: bool
    status: str
    created_at: datetime
    input_schema: tuple[dict[str, Any], ...] = ()
    input_values: dict[str, Any] = field(default_factory=dict)
    preflight_status: str | None = None
    preflight_checks: tuple[AgentDraftCheck, ...] = ()
    preflight_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AgentActionDecision:
    """Immutable human decision plus its separately finalized outcome."""

    decision_id: str
    request_id: str
    conversation_id: str
    actor_user_id: int
    actor_username: str
    operation_code: str
    artifact_name: str | None
    decision: str
    payload_checksum: str
    payload_snapshot: dict[str, Any]
    outcome_status: str
    execution_id: str | None
    failure_summary: str | None
    decided_at: datetime
    finalized_at: datetime | None


@dataclass(frozen=True, slots=True)
class AgentProviderResult:
    """Normalized result returned by any model provider adapter."""

    text: str
    tool_activity: tuple[AgentToolActivity, ...] = ()
    approval_request: AgentApprovalRequest | None = None
    clarification_request: AgentClarificationRequest | None = None
    input_request: AgentInputRequest | None = None
