"""Fail-closed policy for changing the active Oracle application."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.application_workspace import ApplicationWorkspace
from app.products.contracts import BusinessProcessType


@dataclass(frozen=True, slots=True)
class WorkspaceSwitchDecision:
    """Whether one registered workspace is safe in the current runtime."""

    allowed: bool
    reason: str | None = None


def workspace_switch_decision(
    workspace: ApplicationWorkspace,
    *,
    deployment_application_id: int | None,
) -> WorkspaceSwitchDecision:
    """Allow only application targets whose services are request-scoped.

    The deployment application retains the complete existing runtime. FCCS is
    safe outside that deployment because its provider is deliberately
    read-only and creates Oracle clients from the request application context.
    Additional Planning applications remain visible but unavailable until all
    write services and the agent are created per application.
    """

    if workspace.application_id == deployment_application_id:
        return WorkspaceSwitchDecision(allowed=True)
    if workspace.business_process is BusinessProcessType.FCCS:
        return WorkspaceSwitchDecision(allowed=True)
    if workspace.business_process is BusinessProcessType.PLANNING:
        return WorkspaceSwitchDecision(
            allowed=False,
            reason=(
                "Planning execution services are not yet enabled for this "
                "application in the current deployment."
            ),
        )
    return WorkspaceSwitchDecision(
        allowed=False,
        reason=(
            "This Oracle application does not have an enabled product "
            "provider."
        ),
    )
