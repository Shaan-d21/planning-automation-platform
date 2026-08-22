"""Configuration management for the automation framework."""

from app.config.email_settings import EmailNotificationSettings
from app.config.settings import Settings

__all__ = ["EmailNotificationSettings", "Settings"]
