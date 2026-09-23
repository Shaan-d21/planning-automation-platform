"""Safe, stable agent error categories for UI and observability."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.utils.exceptions import (
    AccessControlError,
    AgentCapabilityError,
    AgentConfigurationError,
    AgentConversationError,
    AgentProviderError,
    AuthenticationError,
    DataValidationError,
    EPMConnectionError,
    ExecutionQueueConflictError,
    JobTimeoutError,
)


class AgentErrorCode(StrEnum):
    CONFIGURATION = "AGENT_CONFIGURATION"
    PROVIDER_LIMIT = "MODEL_PROVIDER_LIMIT"
    PROVIDER_UNAVAILABLE = "MODEL_PROVIDER_UNAVAILABLE"
    AUTHENTICATION = "ORACLE_AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION_DENIED"
    VALIDATION = "INPUT_VALIDATION"
    CATALOG_UNAVAILABLE = "CATALOG_UNAVAILABLE"
    CONFLICT = "EXECUTION_CONFLICT"
    TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    INTERNAL = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class ClassifiedAgentError:
    code: AgentErrorCode
    safe_message: str
    retryable: bool

    def as_payload(self) -> dict[str, object]:
        return {
            "error": self.safe_message,
            "error_code": self.code.value,
            "retryable": self.retryable,
        }


def classify_agent_error(error: Exception) -> ClassifiedAgentError:
    """Classify an exception without returning credentials or a stack trace."""
    detail = str(error).casefold()
    if isinstance(error, AgentConfigurationError):
        return ClassifiedAgentError(
            AgentErrorCode.CONFIGURATION,
            "The assistant is not fully configured. Contact an administrator.",
            False,
        )
    if isinstance(error, AgentProviderError):
        limited = any(
            term in detail
            for term in ("rate", "quota", "tokens-per-minute", "temporarily full")
        )
        return ClassifiedAgentError(
            AgentErrorCode.PROVIDER_LIMIT
            if limited else AgentErrorCode.PROVIDER_UNAVAILABLE,
            "The language service is temporarily at its limit. Try again shortly."
            if limited else "The language service could not complete this turn.",
            True,
        )
    if isinstance(error, AuthenticationError):
        return ClassifiedAgentError(
            AgentErrorCode.AUTHENTICATION,
            "Oracle rejected the current environment credentials.",
            False,
        )
    if isinstance(error, AccessControlError):
        return ClassifiedAgentError(
            AgentErrorCode.AUTHORIZATION,
            "Your account is not allowed to perform this platform action.",
            False,
        )
    if isinstance(error, ExecutionQueueConflictError):
        return ClassifiedAgentError(
            AgentErrorCode.CONFLICT,
            "The same target already has active work. Wait for it to finish.",
            True,
        )
    if isinstance(error, JobTimeoutError):
        return ClassifiedAgentError(
            AgentErrorCode.TIMEOUT,
            "Oracle did not reach a terminal status within the allowed time.",
            True,
        )
    if isinstance(error, EPMConnectionError):
        return ClassifiedAgentError(
            AgentErrorCode.UPSTREAM_UNAVAILABLE,
            "The Oracle environment could not be reached.",
            True,
        )
    if isinstance(error, AgentCapabilityError) and "catalog" in detail:
        return ClassifiedAgentError(
            AgentErrorCode.CATALOG_UNAVAILABLE,
            "The current Oracle artifact catalog could not be verified.",
            True,
        )
    if isinstance(
        error,
        (AgentCapabilityError, AgentConversationError, DataValidationError, ValueError),
    ):
        return ClassifiedAgentError(
            AgentErrorCode.VALIDATION,
            str(error) or "The supplied value is invalid.",
            False,
        )
    return ClassifiedAgentError(
        AgentErrorCode.INTERNAL,
        "The assistant could not complete this step.",
        False,
    )

