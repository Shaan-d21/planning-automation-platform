"""Durable execution queue models shared by web and worker processes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ExecutionJobType(StrEnum):
    """Supported durable work categories."""

    OPERATION = "OPERATION"
    PROCESS = "PROCESS"
    STANDALONE_FLOW = "STANDALONE_FLOW"


class ExecutionJobStatus(StrEnum):
    """Lifecycle states for one durable queue record."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {
            self.SUCCESS,
            self.FAILED,
            self.RECOVERY_REQUIRED,
            self.CANCELLED,
        }


@dataclass(frozen=True, slots=True)
class ExecutionJob:
    """One serializable, lease-protected unit of Oracle work."""

    execution_id: str
    job_type: ExecutionJobType
    target_key: str
    payload: dict[str, Any]
    status: ExecutionJobStatus
    priority: int
    attempt_count: int
    created_at: datetime
    available_at: datetime
    claimed_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    cancellation_requested_at: datetime | None = None
    cancellation_requested_by: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionJobSubmission:
    """Validated queue input produced by an HTTP request or scheduler."""

    execution_id: str
    job_type: ExecutionJobType
    target_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    priority: int = 100
