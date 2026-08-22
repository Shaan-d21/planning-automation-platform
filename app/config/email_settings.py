"""Environment-based configuration for task email notifications."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from app.utils.exceptions import ConfigurationError

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True, slots=True)
class EmailNotificationSettings:
    """Validated configuration for the selected email provider."""

    enabled: bool = False
    provider: str = "smtp"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = field(default="", repr=False)
    sender: str = ""
    recipients: tuple[str, ...] = ()
    use_tls: bool = True
    use_ssl: bool = False
    timeout: float = 30.0

    @classmethod
    def from_env(cls) -> EmailNotificationSettings:
        """Load notification settings from the process environment."""
        enabled = cls._parse_bool(
            "EMAIL_NOTIFICATIONS_ENABLED",
            os.getenv("EMAIL_NOTIFICATIONS_ENABLED", "false"),
        )
        provider = os.getenv("EMAIL_PROVIDER", "smtp").strip().casefold()
        smtp_host = os.getenv("EMAIL_SMTP_HOST", "").strip()
        smtp_port = cls._parse_port(
            os.getenv("EMAIL_SMTP_PORT", "587")
        )
        smtp_username = os.getenv("EMAIL_SMTP_USERNAME", "").strip()
        smtp_password = os.getenv("EMAIL_SMTP_PASSWORD", "")
        sender = os.getenv("EMAIL_FROM", "").strip() or smtp_username
        recipients = cls._parse_recipients(os.getenv("EMAIL_TO", ""))
        use_tls = cls._parse_bool(
            "EMAIL_USE_TLS",
            os.getenv("EMAIL_USE_TLS", "true"),
        )
        use_ssl = cls._parse_bool(
            "EMAIL_USE_SSL",
            os.getenv("EMAIL_USE_SSL", "false"),
        )
        timeout = cls._parse_positive_float(
            "EMAIL_SMTP_TIMEOUT",
            os.getenv("EMAIL_SMTP_TIMEOUT", "30"),
        )

        settings = cls(
            enabled=enabled,
            provider=provider,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_username=smtp_username,
            smtp_password=smtp_password,
            sender=sender,
            recipients=recipients,
            use_tls=use_tls,
            use_ssl=use_ssl,
            timeout=timeout,
        )
        settings._validate()
        return settings

    def _validate(self) -> None:
        if not self.enabled:
            return
        if self.provider != "smtp":
            raise ConfigurationError(
                "EMAIL_PROVIDER must be 'smtp'. Corporate providers can be "
                "added through the notification provider interface later."
            )
        if not self.smtp_host:
            raise ConfigurationError(
                "EMAIL_SMTP_HOST is required when notifications are enabled."
            )
        if not self.sender or not _EMAIL_PATTERN.fullmatch(self.sender):
            raise ConfigurationError(
                "EMAIL_FROM must be a valid email address."
            )
        if not self.recipients:
            raise ConfigurationError(
                "EMAIL_TO must contain at least one recipient."
            )
        invalid_recipients = [
            value
            for value in self.recipients
            if not _EMAIL_PATTERN.fullmatch(value)
        ]
        if invalid_recipients:
            raise ConfigurationError(
                "EMAIL_TO contains an invalid email address."
            )
        if bool(self.smtp_username) != bool(self.smtp_password):
            raise ConfigurationError(
                "EMAIL_SMTP_USERNAME and EMAIL_SMTP_PASSWORD must either both "
                "be provided or both be empty."
            )
        if self.use_tls and self.use_ssl:
            raise ConfigurationError(
                "EMAIL_USE_TLS and EMAIL_USE_SSL cannot both be true."
            )

    @staticmethod
    def _parse_recipients(value: str) -> tuple[str, ...]:
        normalized = value.replace(";", ",")
        return tuple(
            item.strip()
            for item in normalized.split(",")
            if item.strip()
        )

    @staticmethod
    def _parse_bool(name: str, value: str) -> bool:
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
        raise ConfigurationError(f"{name} must be true or false.")

    @staticmethod
    def _parse_port(value: str) -> int:
        try:
            port = int(value)
        except ValueError as exc:
            raise ConfigurationError(
                "EMAIL_SMTP_PORT must be a whole number."
            ) from exc
        if port not in range(1, 65_536):
            raise ConfigurationError(
                "EMAIL_SMTP_PORT must be between 1 and 65535."
            )
        return port

    @staticmethod
    def _parse_positive_float(name: str, value: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ConfigurationError(
                f"{name} must be a number greater than zero."
            ) from exc
        if parsed <= 0:
            raise ConfigurationError(
                f"{name} must be a number greater than zero."
            )
        return parsed
