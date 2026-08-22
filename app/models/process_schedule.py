"""Durable scheduling models for approved Planning processes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ScheduleFrequency(StrEnum):
    """Supported recurrence policies."""

    ONE_TIME = "ONE_TIME"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"

    @property
    def display_name(self) -> str:
        return {
            self.ONE_TIME: "One time",
            self.DAILY: "Daily",
            self.WEEKLY: "Weekly",
            self.MONTHLY: "Monthly",
        }[self]


class ScheduleContextMode(StrEnum):
    """How an unattended Process receives runtime context."""

    PIPELINE_DEFAULTS = "PIPELINE_DEFAULTS"
    RUN_PRESET = "RUN_PRESET"

    @property
    def display_name(self) -> str:
        return {
            self.PIPELINE_DEFAULTS: "Oracle Pipeline defaults",
            self.RUN_PRESET: "Saved run preset",
        }[self]


class ScheduleRunOutcome(StrEnum):
    """Most recent scheduler handoff outcome."""

    NEVER = "NEVER"
    SUBMITTED = "SUBMITTED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class ProcessSchedule:
    """One persisted recurrence definition for an approved Process."""

    schedule_id: int
    name: str
    process_code: str
    frequency: ScheduleFrequency
    timezone: str
    first_run_local: datetime
    context_mode: ScheduleContextMode
    preset_id: int | None
    enabled: bool
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_triggered_at: datetime | None = None
    last_execution_id: str | None = None
    last_outcome: ScheduleRunOutcome = ScheduleRunOutcome.NEVER
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class ProcessScheduleInput:
    """Validated configuration used to create or edit a schedule."""

    name: str
    process_code: str
    frequency: ScheduleFrequency
    timezone: str
    first_run_local: datetime
    context_mode: ScheduleContextMode
    preset_id: int | None = None
    enabled: bool = True
