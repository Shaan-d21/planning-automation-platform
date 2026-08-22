"""Tests for environment-based email notification configuration."""

from __future__ import annotations

import pytest

from app.config.email_settings import EmailNotificationSettings
from app.utils.exceptions import ConfigurationError


def test_disabled_notifications_do_not_require_smtp_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMAIL_NOTIFICATIONS_ENABLED", "false")
    monkeypatch.delenv("EMAIL_SMTP_HOST", raising=False)
    monkeypatch.delenv("EMAIL_FROM", raising=False)
    monkeypatch.delenv("EMAIL_TO", raising=False)

    settings = EmailNotificationSettings.from_env()

    assert settings.enabled is False


def test_gmail_smtp_configuration_is_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMAIL_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("EMAIL_PROVIDER", "smtp")
    monkeypatch.setenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("EMAIL_SMTP_PORT", "587")
    monkeypatch.setenv(
        "EMAIL_SMTP_USERNAME",
        "epm.test@gmail.com",
    )
    monkeypatch.setenv("EMAIL_SMTP_PASSWORD", "app-password")
    monkeypatch.setenv("EMAIL_FROM", "epm.test@gmail.com")
    monkeypatch.setenv(
        "EMAIL_TO",
        "first@example.com; second@example.com",
    )
    monkeypatch.setenv("EMAIL_USE_TLS", "true")
    monkeypatch.setenv("EMAIL_USE_SSL", "false")

    settings = EmailNotificationSettings.from_env()

    assert settings.enabled
    assert settings.smtp_host == "smtp.gmail.com"
    assert settings.smtp_port == 587
    assert settings.recipients == (
        "first@example.com",
        "second@example.com",
    )
    assert settings.use_tls


def test_enabled_notifications_require_recipient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMAIL_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("EMAIL_FROM", "epm.test@gmail.com")
    monkeypatch.setenv("EMAIL_TO", "")

    with pytest.raises(ConfigurationError, match="EMAIL_TO"):
        EmailNotificationSettings.from_env()


def test_tls_and_implicit_ssl_are_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMAIL_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("EMAIL_FROM", "epm.test@gmail.com")
    monkeypatch.setenv("EMAIL_TO", "recipient@example.com")
    monkeypatch.setenv("EMAIL_USE_TLS", "true")
    monkeypatch.setenv("EMAIL_USE_SSL", "true")

    with pytest.raises(ConfigurationError, match="cannot both"):
        EmailNotificationSettings.from_env()
