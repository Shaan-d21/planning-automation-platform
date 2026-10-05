"""Read-only FCCS provider backed by verified Oracle REST resources."""

from __future__ import annotations

from app.products.contracts import (
    BusinessProcessType,
    CapabilityDefinition,
    CapabilityScope,
    NavigationDefinition,
    OperationDefinition,
)


class FCCSProductProvider:
    """Expose verified FCCS reads without advertising write operations."""

    business_process = BusinessProcessType.FCCS
    enabled = True

    def operations(self) -> tuple[OperationDefinition, ...]:
        return ()

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
            ),
            CapabilityDefinition(
                code="fccs-dimension-discovery",
                scope=CapabilityScope.PRODUCT,
                description="Review FCCS cubes and dimension metadata.",
            ),
            CapabilityDefinition(
                code="fccs-job-discovery",
                scope=CapabilityScope.PRODUCT,
                description=(
                    "Review FCCS job definitions and exact Oracle job status."
                ),
            ),
            CapabilityDefinition(
                code="fccs-journal-review",
                scope=CapabilityScope.PRODUCT,
                description=(
                    "Review consolidation journals and journal line items."
                ),
            ),
        )
