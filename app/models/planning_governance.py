"""Governed Planning approval and in-application notification models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.models.planning_validation import PlanningValidationEvidence


class PlanningApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    RETURNED = "RETURNED"
    CANCELLED = "CANCELLED"


class NotificationSeverity(StrEnum):
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class PlanningApproval:
    approval_id: int
    cycle_id: int
    cycle_name: str
    submitted_task_id: int
    submitted_task_title: str
    approval_task_id: int
    approval_task_title: str
    entity: str | None
    scenario: str | None
    period: str | None
    status: PlanningApprovalStatus
    submitted_by_user_id: int
    submitted_by_name: str
    submitted_at: datetime
    decided_by_user_id: int | None
    decided_at: datetime | None
    decision_comment: str | None
    validation: PlanningValidationEvidence | None = None


@dataclass(frozen=True, slots=True)
class UserNotification:
    notification_id: int
    recipient_user_id: int
    event_type: str
    severity: NotificationSeverity
    title: str
    message: str
    action_url: str | None
    source_type: str | None
    source_id: str | None
    created_at: datetime
    read_at: datetime | None
