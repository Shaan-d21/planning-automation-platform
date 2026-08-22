"""Provider-neutral task notifications with an SMTP implementation."""

from __future__ import annotations

import logging
import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from typing import Protocol

from app.config.email_settings import EmailNotificationSettings
from app.models.notification import TaskNotificationEvent
from app.utils.exceptions import NotificationError

SMTPFactory = Callable[..., smtplib.SMTP]
SMTPSSLFactory = Callable[..., smtplib.SMTP_SSL]


class NotificationProvider(Protocol):
    """Provider contract for future SMTP, Graph, or other adapters."""

    def send(self, event: TaskNotificationEvent) -> None:
        """Send one terminal task event."""


class NullNotificationProvider:
    """No-op provider used when email notifications are disabled."""

    def send(self, event: TaskNotificationEvent) -> None:
        """Intentionally ignore the event."""


class SMTPNotificationProvider:
    """Send plain-text task notifications using SMTP."""

    def __init__(
        self,
        settings: EmailNotificationSettings,
        *,
        smtp_factory: SMTPFactory = smtplib.SMTP,
        smtp_ssl_factory: SMTPSSLFactory = smtplib.SMTP_SSL,
    ) -> None:
        self._settings = settings
        self._smtp_factory = smtp_factory
        self._smtp_ssl_factory = smtp_ssl_factory

    def send(self, event: TaskNotificationEvent) -> None:
        """Build and send an RFC-compliant email for the supplied event."""
        message = EmailMessage()
        message["Subject"] = event.subject
        message["From"] = self._settings.sender
        message["To"] = ", ".join(self._settings.recipients)
        message.set_content(event.as_plain_text())

        factory: SMTPFactory | SMTPSSLFactory = (
            self._smtp_ssl_factory
            if self._settings.use_ssl
            else self._smtp_factory
        )
        try:
            with factory(
                self._settings.smtp_host,
                self._settings.smtp_port,
                timeout=self._settings.timeout,
            ) as connection:
                connection.ehlo()
                if self._settings.use_tls:
                    connection.starttls(context=ssl.create_default_context())
                    connection.ehlo()
                if self._settings.smtp_username:
                    connection.login(
                        self._settings.smtp_username,
                        self._settings.smtp_password,
                    )
                connection.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise NotificationError(
                f"SMTP notification could not be sent: {exc}"
            ) from exc


class NotificationService:
    """Publish events without allowing email failures to mask task results."""

    def __init__(
        self,
        provider: NotificationProvider,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._provider = provider
        self._logger = logger or logging.getLogger(__name__)

    def publish(self, event: TaskNotificationEvent) -> bool:
        """Send an event and return whether notification delivery succeeded."""
        try:
            self._provider.send(event)
        except Exception as exc:
            self._logger.error(
                "Task notification failed without changing task status: %s",
                exc,
            )
            return False
        self._logger.info(
            "Task notification processed: task='%s', status='%s'.",
            event.task_name,
            event.status.value,
        )
        return True


def create_notification_service(
    settings: EmailNotificationSettings,
    *,
    logger: logging.Logger | None = None,
) -> NotificationService:
    """Create the configured provider behind the stable notification service."""
    provider: NotificationProvider
    if settings.enabled:
        provider = SMTPNotificationProvider(settings)
    else:
        provider = NullNotificationProvider()
    return NotificationService(provider, logger=logger)
