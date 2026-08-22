"""Models for external command execution."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Normalized result returned by an external command."""

    command: str
    return_code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def is_successful(self) -> bool:
        """Return whether the process exited successfully."""
        return self.return_code == 0

    @property
    def details(self) -> str:
        """Return the most useful bounded command output."""
        value = self.stderr.strip() or self.stdout.strip()
        return " ".join(value.split())[:1000] or "No details returned."
