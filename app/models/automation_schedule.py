"""Generic durable scheduling models for platform-owned automation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class AutomationTargetType(StrEnum):
    """Allowlisted unattended work that the platform may schedule."""

    ORACLE_PIPELINE = "ORACLE_PIPELINE"
    RTP_REGISTRY_SYNC = "RTP_REGISTRY_SYNC"


class AutomationScheduleFrequency(StrEnum):
    """Supported recurrence policies."""

    ONE_TIME = "ONE_TIME"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class AutomationInputPolicy(StrEnum):
    """How unattended execution resolves target inputs."""

    ORACLE_DEFAULTS = "ORACLE_DEFAULTS"
    FIXED = "FIXED"
    DYNAMIC = "DYNAMIC"


class AutomationConcurrencyPolicy(StrEnum):
    """Behavior when the same target already has active work."""

    SKIP_IF_ACTIVE = "SKIP_IF_ACTIVE"


class AutomationMisfirePolicy(StrEnum):
    """Behavior when the scheduler observes an overdue occurrence."""

    RUN_ONCE = "RUN_ONCE"
    SKIP = "SKIP"


class AutomationScheduleOutcome(StrEnum):
    """Latest schedule handoff outcome."""

    NEVER = "NEVER"
    CLAIMED = "CLAIMED"
    SUBMITTED = "SUBMITTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class AutomationScheduleRunStatus(StrEnum):
    """Lifecycle of one durable scheduled occurrence."""

    CLAIMED = "CLAIMED"
    SUBMITTED = "SUBMITTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class AutomationSchedule:
    """One active or paused platform-owned recurrence."""

    schedule_id: int
    environment_key: str
    name: str
    target_type: AutomationTargetType
    target_key: str
    frequency: AutomationScheduleFrequency
    timezone: str
    first_run_local: datetime
    input_policy: AutomationInputPolicy
    configuration: dict[str, Any]
    concurrency_policy: AutomationConcurrencyPolicy
    misfire_policy: AutomationMisfirePolicy
    enabled: bool
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_triggered_at: datetime | None = None
    last_execution_id: str | None = None
    last_outcome: AutomationScheduleOutcome = AutomationScheduleOutcome.NEVER
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class AutomationScheduleInput:
    """Validated configuration used to create or edit a schedule."""

    environment_key: str
    name: str
    target_type: AutomationTargetType
    target_key: str
    frequency: AutomationScheduleFrequency
    timezone: str
    first_run_local: datetime
    input_policy: AutomationInputPolicy = AutomationInputPolicy.ORACLE_DEFAULTS
    configuration: dict[str, Any] = field(default_factory=dict)
    concurrency_policy: AutomationConcurrencyPolicy = (
        AutomationConcurrencyPolicy.SKIP_IF_ACTIVE
    )
    misfire_policy: AutomationMisfirePolicy = AutomationMisfirePolicy.RUN_ONCE
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class AutomationScheduleRun:
    """One claimed occurrence and its scheduler handoff evidence."""

    run_id: int
    schedule_id: int
    scheduled_for: datetime
    claimed_at: datetime
    status: AutomationScheduleRunStatus
    resolved_payload: dict[str, Any]
    completed_at: datetime | None = None
    execution_id: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class AutomationScheduleRunEvidence:
    """One occurrence enriched with its durable schedule identity."""

    run: AutomationScheduleRun
    schedule_name: str
    target_type: AutomationTargetType
    target_key: str


@dataclass(frozen=True, slots=True)
class AutomationSchedulePreview:
    """Validated next occurrence shown before persistence."""

    next_run_at: datetime
    next_run_local: datetime
