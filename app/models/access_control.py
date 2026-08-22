"""Identity and authorization models for the automation platform."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Permission(StrEnum):
    """Server-enforced capabilities exposed by the platform."""

    PROCESS_RUN = "process.run"
    PROCESS_DESIGN = "process.design"
    SCHEDULE_MANAGE = "schedule.manage"
    OPERATION_EXECUTE = "operation.execute"
    VARIABLE_UPDATE = "variable.update"
    USER_VARIABLE_UPDATE = "user_variable.update"
    DATA_REVIEW = "data.review"
    REPORT_GENERATE = "report.generate"
    HISTORY_VIEW = "history.view"
    USER_MANAGE = "user.manage"
    CATALOG_MANAGE = "catalog.manage"
    AGENT_USE = "agent.use"


class RoleCode(StrEnum):
    """Stable codes for the four business-facing platform roles."""

    SERVICE_ADMINISTRATOR = "SERVICE_ADMINISTRATOR"
    POWER_USER = "POWER_USER"
    USER = "USER"
    VIEWER = "VIEWER"


class TriggerSource(StrEnum):
    """Origin of an automation request for durable audit attribution."""

    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"
    API = "API"
    EXCEL = "EXCEL"
    AI_AGENT = "AI_AGENT"


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    """One role and its granted capabilities."""

    code: RoleCode
    name: str
    description: str
    permissions: frozenset[Permission]


@dataclass(frozen=True, slots=True)
class UserAccount:
    """A platform identity resolved with roles and permissions."""

    user_id: int
    username: str
    display_name: str
    email: str | None
    active: bool
    roles: tuple[RoleCode, ...]
    permissions: frozenset[Permission]
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None

    def has_permission(self, permission: Permission) -> bool:
        """Return whether this active identity owns a capability."""
        return self.active and permission in self.permissions


@dataclass(frozen=True, slots=True)
class ExecutionActor:
    """Minimal immutable identity copied into workflow audit history."""

    username: str
    display_name: str
    trigger_source: TriggerSource = TriggerSource.MANUAL
