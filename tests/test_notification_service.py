"""Tests for provider-neutral task email notifications."""

from __future__ import annotations

import smtplib
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock

from app.config.email_settings import EmailNotificationSettings
from app.models.notification import (
    TaskNotificationEvent,
    TaskNotificationStatus,
)
from app.services.notification_service import (
    NotificationService,
    SMTPNotificationProvider,
)


def notification_event(
    status: TaskNotificationStatus = TaskNotificationStatus.SUCCESS,
) -> TaskNotificationEvent:
    return TaskNotificationEvent(
        task_name="Data Integration load",
        status=status,
        environment_url="https://example.oraclecloud.com",
        application_name="Plan1",
        execution_engine="rest",
        occurred_at=datetime(2026, 7, 25, 8, 30, tzinfo=timezone.utc),
        duration_seconds=12.5,
        job_or_integration_name="Test_DataLoad",
        file_name="Test.csv",
        period_range="{Apr-26}",
        error_message=(
            "Oracle could not open the input file."
            if status is TaskNotificationStatus.FAILED
            else None
        ),
    )


def smtp_settings() -> EmailNotificationSettings:
    return EmailNotificationSettings(
        enabled=True,
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_username="epm.test@gmail.com",
        smtp_password="secret-app-password",
        sender="epm.test@gmail.com",
        recipients=("recipient@example.com",),
        use_tls=True,
    )


def test_smtp_provider_uses_starttls_login_and_email_message() -> None:
    smtp_factory = MagicMock()
    connection = smtp_factory.return_value.__enter__.return_value
    provider = SMTPNotificationProvider(
        smtp_settings(),
        smtp_factory=smtp_factory,
    )

    provider.send(notification_event())

    smtp_factory.assert_called_once_with(
        "smtp.gmail.com",
        587,
        timeout=30.0,
    )
    connection.starttls.assert_called_once()
    connection.login.assert_called_once_with(
        "epm.test@gmail.com",
        "secret-app-password",
    )
    message = connection.send_message.call_args.args[0]
    assert message["Subject"] == (
        "[Oracle EPM] SUCCESS: Data Integration load - Test_DataLoad"
    )
    assert "Periods: {Apr-26}" in message.get_content()
    assert "secret-app-password" not in message.as_string()


def test_notification_failure_does_not_raise_or_change_task_status() -> None:
    provider = Mock()
    provider.send.side_effect = smtplib.SMTPException("temporary failure")
    logger = Mock()
    service = NotificationService(provider, logger=logger)

    delivered = service.publish(
        notification_event(TaskNotificationStatus.FAILED)
    )

    assert delivered is False
    logger.error.assert_called_once()


def test_failure_message_contains_operational_error() -> None:
    body = notification_event(
        TaskNotificationStatus.FAILED
    ).as_plain_text()

    assert "Status: FAILED" in body
    assert "Error: Oracle could not open the input file." in body
