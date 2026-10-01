from app.agent.errors import AgentErrorCode, classify_agent_error
from app.utils.exceptions import (
    AgentCapabilityError,
    AgentProviderError,
    AuthenticationError,
)


def test_provider_limit_is_retryable_and_does_not_echo_raw_details() -> None:
    result = classify_agent_error(
        AgentProviderError("rate limit for secret organization org-123")
    )

    assert result.code is AgentErrorCode.PROVIDER_LIMIT
    assert result.retryable is True
    assert "org-123" not in result.safe_message


def test_authentication_error_has_stable_non_retryable_category() -> None:
    result = classify_agent_error(
        AuthenticationError("invalid password user=admin")
    )

    assert result.code is AgentErrorCode.AUTHENTICATION
    assert result.retryable is False
    assert "password" not in result.safe_message.casefold()


def test_catalog_error_is_distinct_from_value_validation() -> None:
    catalog = classify_agent_error(
        AgentCapabilityError("The Oracle artifact catalog is unavailable")
    )
    invalid = classify_agent_error(AgentCapabilityError("Choose a valid scope"))

    assert catalog.code is AgentErrorCode.CATALOG_UNAVAILABLE
    assert invalid.code is AgentErrorCode.VALIDATION

