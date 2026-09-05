"""Typed events used by task notification providers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class TaskNotificationStatus(StrEnum):
    """Terminal task states that can produce a notification."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class TaskNotificationEvent:
    """Provider-neutral details for one completed automation task."""

    task_name: str
    status: TaskNotificationStatus
    environment_url: str
    application_name: str
    execution_engine: str
    occurred_at: datetime
    duration_seconds: float
    job_or_integration_name: str | None = None
    file_name: str | None = None
    period_range: str | None = None
    error_message: str | None = None

    @property
    def subject(self) -> str:
        """Return a concise subject suitable for operational alerts."""
        target = (
            f" - {self.job_or_integration_name}"
            if self.job_or_integration_name
            else ""
        )
        return (
            f"[Oracle EPM] {self.status.value}: "
            f"{self.task_name}{target}"
        )

    def as_plain_text(self) -> str:
        """Render a stable beginner-friendly plain-text email body."""
        rows: list[tuple[str, str]] = [
            ("Task", self.task_name),
            ("Status", self.status.value),
            ("Environment", self.environment_url),
            ("Application", self.application_name),
            ("Execution engine", self.execution_engine),
            ("Completed at", self.occurred_at.isoformat(timespec="seconds")),
            ("Duration", f"{self.duration_seconds:.2f} seconds"),
        ]
        optional_rows = (
            ("Job/Integration", self.job_or_integration_name),
            ("File", self.file_name),
            ("Periods", self.period_range),
            ("Error", self.error_message),
        )
        rows.extend(
            (label, value)
            for label, value in optional_rows
            if value
        )
        return "\n".join(f"{label}: {value}" for label, value in rows)
