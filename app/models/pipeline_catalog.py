"""Configuration model for selectable Oracle pipelines."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PipelineCatalogDefinition:
    """One administrator-managed pipeline menu entry."""

    code: str
    name: str
    description: str | None = None

    @property
    def display_label(self) -> str:
        """Return a descriptive menu label while keeping the code visible."""
        label = f"{self.name} ({self.code})"
        if self.description:
            return f"{label} - {self.description}"
        return label
