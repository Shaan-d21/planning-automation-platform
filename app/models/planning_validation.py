"""Governed, summary-only evidence for Planning data-review tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class PlanningValidationType(StrEnum):
    QUALITY = "QUALITY"
    COMPARISON = "COMPARISON"


class PlanningValidationStatus(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class PlanningValidationEvidence:
    """Audit evidence without storing Planning cell values."""

    validation_id: int
    task_id: int
    validation_type: PlanningValidationType
    status: PlanningValidationStatus
    source_cube: str
    target_cube: str | None
    selection: dict[str, Any]
    criteria: dict[str, Any]
    checked_cells: int
    matched_cells: int | None
    exception_count: int
    warning_count: int
    performed_by_user_id: int
    performed_at: datetime
    warning_acknowledged_by_user_id: int | None = None
    warning_acknowledged_at: datetime | None = None

    @property
    def completion_allowed(self) -> bool:
        return self.status is PlanningValidationStatus.PASS or (
            self.status is PlanningValidationStatus.WARNING
            and self.warning_acknowledged_at is not None
        )
