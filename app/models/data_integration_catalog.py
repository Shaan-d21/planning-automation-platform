"""Configuration models for selectable Oracle Data Integrations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DataIntegrationDefinition:
    """One administrator-managed Data Integration menu entry."""

    name: str
    description: str | None = None

    @property
    def display_label(self) -> str:
        """Return the user-facing menu label."""
        if self.description:
            return f"{self.name} - {self.description}"
        return self.name
