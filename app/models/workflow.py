"""Typed models for reusable Oracle EPM automation workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.models.access_control import TriggerSource


class WorkflowStatus(StrEnum):
    """Terminal and non-terminal workflow states."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class WorkflowStepStatus(StrEnum):
    """Execution states for an individual workflow step."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class WorkflowStepResult:
    """Durable result of one workflow step."""

    name: str
    sequence: int
    status: WorkflowStepStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    details: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    """Durable execution record for one workflow run."""

    execution_id: str
    workflow_name: str
    status: WorkflowStatus
    started_at: datetime
    completed_at: datetime | None = None
    steps: tuple[WorkflowStepResult, ...] = ()
    error_message: str | None = None
    initiated_by: str | None = None
    initiated_by_display: str | None = None
    trigger_source: TriggerSource = TriggerSource.API
