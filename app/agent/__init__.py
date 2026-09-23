"""Provider-neutral AI agent subsystem for the EPM platform.

Keep package initialization lightweight.  Application and worker modules use
agent repositories and value objects without needing to initialize the full
agent service graph.  The public service export remains available lazily for
callers that import it from :mod:`app.agent`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent.service import AgentApplicationService

__all__ = ["AgentApplicationService"]


def __getattr__(name: str) -> Any:
    if name == "AgentApplicationService":
        from app.agent.service import AgentApplicationService

        return AgentApplicationService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
