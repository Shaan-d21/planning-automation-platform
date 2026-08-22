"""Operational Planning-cycle and user-task domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class OperationalCycleStatus(StrEnum):
    """Business lifecycle state of one dated Planning cycle."""

    DRAFT = "DRAFT"
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    REVIEW = "REVIEW"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class CycleStageStatus(StrEnum):
    """Progress state of one business stage within a cycle."""

    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"


class PlanningTaskStatus(StrEnum):
    """Persisted state controlled by an assigned user or administrator."""

    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class PlanningTaskPriority(StrEnum):
    """Business priority used to order attention queues."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class PlanningTaskReadiness(StrEnum):
    """Computed readiness without duplicating dependency state in storage."""

    READY = "READY"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class PlanningTaskExecutionStatus(StrEnum):
    """Lifecycle of one Oracle execution attempt linked to a task."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class PlanningCycleRecord:
    """One dated operational cycle, distinct from a reusable definition."""

    cycle_id: int
    code: str
    name: str
    cycle_type: str
    process_code: str | None
    scenario: str | None
    year: str
    actual_through_period: str | None
    forecast_start_period: str | None
    start_date: date
    due_date: date
    status: OperationalCycleStatus
    created_by_user_id: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PlanningCycleStage:
    """One business stage used for lifecycle progress, not Pipeline internals."""

    stage_id: int
    cycle_id: int
    sequence: int
    code: str
    name: str
    status: CycleStageStatus
    start_date: date | None = None
    due_date: date | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PlanningTask:
    """One actionable assignment presented to a business or support user."""

    task_id: int
    stage_id: int
    cycle_id: int
    cycle_code: str
    cycle_name: str
    stage_code: str
    stage_name: str
    title: str
    description: str
    task_type: str
    status: PlanningTaskStatus
    priority: PlanningTaskPriority
    assigned_user_id: int | None
    assigned_role_code: str | None
    entity: str | None
    scenario: str | None
    period: str | None
    due_at: datetime | None
    action_type: str
    action_config: dict[str, Any]
    dependency_ids: tuple[int, ...] = ()
    incomplete_dependency_ids: tuple[int, ...] = ()
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def readiness(self) -> PlanningTaskReadiness:
        if self.status is PlanningTaskStatus.COMPLETED:
            return PlanningTaskReadiness.COMPLETED
        if self.status is PlanningTaskStatus.CANCELLED:
            return PlanningTaskReadiness.CANCELLED
        if self.status is PlanningTaskStatus.BLOCKED:
            return PlanningTaskReadiness.BLOCKED
        if self.incomplete_dependency_ids:
            return PlanningTaskReadiness.WAITING
        return PlanningTaskReadiness.READY


@dataclass(frozen=True, slots=True)
class PlanningTaskExecution:
    """Durable link between a business task and one retained workflow run."""

    task_execution_id: int
    task_id: int
    execution_id: str
    attempt_number: int
    status: PlanningTaskExecutionStatus
    initiated_by_user_id: int
    linked_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class CycleStageDraft:
    """Validated stage supplied while creating an operational cycle."""

    code: str
    name: str
    sequence: int
    start_date: date | None = None
    due_date: date | None = None


@dataclass(frozen=True, slots=True)
class PlanningTaskDraft:
    """Validated task supplied with client keys for dependency resolution."""

    key: str
    stage_code: str
    title: str
    description: str
    task_type: str
    priority: PlanningTaskPriority
    action_type: str
    assigned_username: str | None = None
    assigned_role_code: str | None = None
    entity: str | None = None
    scenario: str | None = None
    period: str | None = None
    due_at: datetime | None = None
    action_config: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlanningCycleDraft:
    """Atomic command for creating a cycle, stages, tasks, and dependencies."""

    code: str
    name: str
    cycle_type: str
    year: str
    start_date: date
    due_date: date
    stages: tuple[CycleStageDraft, ...]
    tasks: tuple[PlanningTaskDraft, ...]
    process_code: str | None = None
    scenario: str | None = None
    actual_through_period: str | None = None
    forecast_start_period: str | None = None
