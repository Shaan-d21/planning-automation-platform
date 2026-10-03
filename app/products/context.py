"""Verified Oracle application classification for product selection."""

from __future__ import annotations

import re

from app.models.environment import ApplicationInfo
from app.products.contracts import BusinessProcessType, ProductContext


_FCCS_MARKERS = frozenset(
    {
        "FCCS",
        "FINANCIAL CONSOLIDATION AND CLOSE",
        "FINANCIAL CONSOLIDATION",
        "CONSOLIDATION AND CLOSE",
    }
)
_PLANNING_MARKERS = frozenset(
    {
        "PBCS",
        "EPBCS",
        "PLANNING",
        "PLANNING MODULES",
    }
)


def classify_business_process(
    *,
    product_type: str | None,
    application_type: str | None,
) -> BusinessProcessType:
    """Classify only explicit Oracle metadata; never infer from app names."""

    values = tuple(
        normalized
        for raw in (application_type, product_type)
        if (normalized := _normalize_marker(raw))
    )
    if any(_contains_marker(value, _FCCS_MARKERS) for value in values):
        return BusinessProcessType.FCCS
    if any(_contains_marker(value, _PLANNING_MARKERS) for value in values):
        return BusinessProcessType.PLANNING
    return BusinessProcessType.UNKNOWN


def product_context(application: ApplicationInfo) -> ProductContext:
    """Build a product context from one Oracle-verified application."""

    return ProductContext(
        business_process=classify_business_process(
            product_type=application.product_type,
            application_type=application.application_type,
        ),
        application_name=application.name,
        product_type=application.product_type,
        application_type=application.application_type,
    )


def _normalize_marker(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()


def _contains_marker(value: str, markers: frozenset[str]) -> bool:
    padded = f" {value} "
    return any(f" {marker} " in padded for marker in markers)
