"""Read-only FCCS provider backed by verified Oracle REST resources."""

from __future__ import annotations

from app.products.contracts import (
    BusinessProcessType,
    CapabilityDefinition,
    CapabilityScope,
    NavigationDefinition,
    OperationDefinition,
    OperationKind,
)


class FCCSProductProvider:
    """Expose verified FCCS reads without advertising write operations."""

    business_process = BusinessProcessType.FCCS
    enabled = True

    def operations(self) -> tuple[OperationDefinition, ...]:
        return (
            OperationDefinition(
                kind=OperationKind.FCCS_CONSOLIDATION,
                code="fccs-consolidation",
                display_name="Run FCCS Consolidation",
                description=(
                    "Run one live-discovered FCCS consolidation rule for an "
                    "exact Scenario, Year, Period, and Entity POV."
                ),
                category="Consolidation",
                risk_level="Elevated",
                route="/app/assistant",
            ),
            OperationDefinition(
                kind=OperationKind.FCCS_JOURNAL_ACTION,
                code="fccs-journal-action",
                display_name="FCCS Journal Action",
                description=(
                    "Submit, approve, post, unpost, or reject one exact FCCS "
                    "journal after live review and explicit approval."
                ),
                category="Consolidation journals",
                risk_level="Elevated",
                route="/app/assistant",
            ),
        )

    def navigation(self) -> tuple[NavigationDefinition, ...]:
        return (
            NavigationDefinition(
                "fccs-overview",
                "Close Overview",
                "#fccs-overview",
                "consolidation",
                20,
                ("HISTORY_VIEW",),
            ),
            NavigationDefinition(
                "fccs-dimensions",
                "Dimensions",
                "#fccs-dimensions",
                "consolidation",
                30,
                ("HISTORY_VIEW",),
            ),
            NavigationDefinition(
                "fccs-jobs",
                "Oracle Jobs",
                "#fccs-jobs",
                "consolidation",
                40,
                ("HISTORY_VIEW",),
            ),
            NavigationDefinition(
                "fccs-journals",
                "Journals",
                "#fccs-journals",
                "consolidation",
                50,
                ("HISTORY_VIEW",),
            ),
        )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition(
                code="fccs-application-overview",
                scope=CapabilityScope.PRODUCT,
                description=(
                    "Verify the active Financial Consolidation and Close "
                    "application through supported Oracle APIs."
                ),
                agent_tools=("get_fccs_overview",),
            ),
            CapabilityDefinition(
                code="fccs-dimension-discovery",
                scope=CapabilityScope.PRODUCT,
                description="Review FCCS cubes and dimension metadata.",
                agent_tools=("list_fccs_dimensions",),
            ),
            CapabilityDefinition(
                code="fccs-job-discovery",
                scope=CapabilityScope.PRODUCT,
                description=(
                    "Review FCCS job definitions and exact Oracle job status."
                ),
                agent_tools=("list_fccs_jobs", "get_fccs_job"),
            ),
            CapabilityDefinition(
                code="fccs-journal-review",
                scope=CapabilityScope.PRODUCT,
                description=(
                    "Review consolidation journals and journal line items."
                ),
                agent_tools=("list_fccs_journals", "get_fccs_journal"),
            ),
            CapabilityDefinition(
                code="fccs-governed-consolidation",
                scope=CapabilityScope.PRODUCT,
                description="Prepare and monitor a governed FCCS consolidation.",
                agent_tools=(
                    "list_fccs_jobs",
                    "list_platform_operations",
                    "prepare_operation_action",
                ),
            ),
            CapabilityDefinition(
                code="fccs-governed-journal-actions",
                scope=CapabilityScope.PRODUCT,
                description="Prepare governed FCCS journal workflow actions.",
                agent_tools=("list_platform_operations", "prepare_operation_action"),
            ),
        )
