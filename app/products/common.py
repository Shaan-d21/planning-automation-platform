"""Capabilities and navigation shared by every supported EPM product."""

from __future__ import annotations

from app.products.contracts import (
    CapabilityDefinition,
    CapabilityScope,
    NavigationDefinition,
)


COMMON_EPM_CAPABILITIES = (
    CapabilityDefinition(
        code="environment-verification",
        scope=CapabilityScope.COMMON,
        description="Verify an Oracle EPM environment connection.",
        agent_tools=("get_environment_summary",),
    ),
    CapabilityDefinition(
        code="application-discovery",
        scope=CapabilityScope.COMMON,
        description=(
            "Discover Oracle applications visible to the configured account."
        ),
    ),
    CapabilityDefinition(
        code="dimension-discovery",
        scope=CapabilityScope.COMMON,
        description="Read application dimensions through a product adapter.",
    ),
    CapabilityDefinition(
        code="job-monitoring",
        scope=CapabilityScope.COMMON,
        description=(
            "Read and monitor Oracle job status and retained evidence."
        ),
        agent_tools=("get_recent_execution_history", "get_execution_evidence"),
    ),
)


# Order values deliberately preserve the existing Planning shell contract when
# these entries are combined with Planning-specific navigation.
COMMON_NAVIGATION_DEFINITIONS = (
    NavigationDefinition("home", "Home", "#home", "workspace", 10),
    NavigationDefinition(
        "notifications", "Notifications", "#notifications", "workspace", 30
    ),
    NavigationDefinition(
        "jobs",
        "Jobs & Activity",
        "#jobs",
        "analysis",
        90,
        ("HISTORY_VIEW",),
    ),
    NavigationDefinition(
        "assistant",
        "EPM Assistant",
        "#assistant",
        "workspace",
        100,
        ("AGENT_USE",),
    ),
    NavigationDefinition(
        "access-control",
        "Access Control",
        "#access",
        "administration",
        120,
        ("USER_MANAGE",),
    ),
    NavigationDefinition(
        "system-administration",
        "System Administration",
        "#system-administration",
        "administration",
        130,
        ("SECURITY_AUDIT_VIEW",),
    ),
)
