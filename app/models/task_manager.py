"""Normalized Oracle Task Manager synchronization models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class TaskManagerSource:
    """Saved Oracle report configuration for one application workspace."""

    application_id: int
    report_group: str
    report_name: str
    parameters: dict[str, str]
    updated_at: datetime
    last_synced_at: datetime | None = None
    last_sync_status: str | None = None
    last_sync_record_count: int = 0
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class TaskManagerTask:
    """One task normalized from an Oracle Task Manager report row."""

    source_key: str
    name: str
    external_id: str | None = None
    schedule_name: str | None = None
    period_name: str | None = None
    status: str | None = None
    owner: str | None = None
    assignee: str | None = None
    approver: str | None = None
    organization: str | None = None
    task_type: str | None = None
    priority: str | None = None
    description: str | None = None
    parent_task: str | None = None
    dependency: str | None = None
    start_at: datetime | None = None
    due_at: datetime | None = None
    completed_at: datetime | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TaskManagerSnapshot:
    """Current persisted Task Manager state for one Oracle application."""

    source: TaskManagerSource | None
    tasks: tuple[TaskManagerTask, ...]


@dataclass(frozen=True, slots=True)
class TaskManagerSyncResult:
    """Evidence returned after replacing a normalized task snapshot."""

    synchronized_at: datetime
    record_count: int
    schedule_count: int
    status_counts: dict[str, int]

