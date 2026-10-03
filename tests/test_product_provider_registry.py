"""Regression contracts for the product-provider modularization seam."""

from __future__ import annotations

import pytest

from app.application.operations import (
    OPERATION_DEFINITIONS,
    OperationCatalogService,
)
from app.models.environment import ApplicationInfo
from app.products.context import classify_business_process, product_context
from app.products.contracts import BusinessProcessType
from app.products.registry import PRODUCT_PROVIDER_REGISTRY, ProductProviderRegistry


PLANNING_OPERATION_CONTRACT = (
    (
        "REPORT_GENERATION",
        "report-generation",
        "Data Explorer Export",
        "Analysis",
        "Read only",
        "/app/reports",
    ),
    (
        "CUBE_REFRESH",
        "cube-refresh",
        "Planning Cube Refresh",
        "Application administration",
        "Elevated",
        "/app/operations/cube-refresh",
    ),
    (
        "SUBSTITUTION_VARIABLE",
        "substitution-variables",
        "Substitution Variables",
        "Application administration",
        "Elevated",
        "/app/operations/substitution-variables",
    ),
    (
        "USER_VARIABLE",
        "user-variables",
        "User Variables",
        "User preferences",
        "Controlled",
        "/app/operations/user-variables",
    ),
    (
        "DATA_IMPORT",
        "data-import",
        "Planning Data Import",
        "Data loading",
        "Elevated",
        "/app/operations/data-import",
    ),
    (
        "METADATA_IMPORT",
        "metadata-import",
        "Metadata Import",
        "Application administration",
        "Elevated",
        "/app/operations/metadata-import",
    ),
    (
        "PIPELINE",
        "pipelines",
        "Pipelines",
        "Orchestration",
        "Elevated",
        "/app/operations/pipelines",
    ),
    (
        "DATA_INTEGRATION",
        "data-integrations",
        "Data Integrations",
        "Data loading",
        "Elevated",
        "/app/operations/data-integrations",
    ),
    (
        "BUSINESS_RULE",
        "business-rules",
        "Business Rules",
        "Calculation",
        "Controlled",
        "/app/operations/business-rules",
    ),
    (
        "DATA_MAP",
        "data-maps",
        "Data Maps",
        "Data movement",
        "Elevated",
        "/app/operations/data-maps",
    ),
)


def test_planning_operation_contract_is_unchanged() -> None:
    actual = tuple(
        (
            item.kind.value,
            item.code,
            item.display_name,
            item.category,
            item.risk_level,
            item.route,
        )
        for item in OPERATION_DEFINITIONS
    )

    assert actual == PLANNING_OPERATION_CONTRACT
    assert OperationCatalogService.definitions() == OPERATION_DEFINITIONS
    assert (
        PRODUCT_PROVIDER_REGISTRY.operations_for(BusinessProcessType.PLANNING)
        == OPERATION_DEFINITIONS
    )


def test_fccs_provider_is_registered_but_cannot_expose_operations() -> None:
    assert PRODUCT_PROVIDER_REGISTRY.get(BusinessProcessType.FCCS) is None
    provider = PRODUCT_PROVIDER_REGISTRY.get(
        BusinessProcessType.FCCS,
        include_disabled=True,
    )

    assert provider is not None
    assert provider.enabled is False
    assert provider.operations() == ()


def test_unknown_product_never_inherits_planning_operations() -> None:
    assert (
        PRODUCT_PROVIDER_REGISTRY.operations_for(BusinessProcessType.UNKNOWN)
        == ()
    )


@pytest.mark.parametrize(
    ("product_type", "application_type", "expected"),
    (
        ("HP", "PBCS", BusinessProcessType.PLANNING),
        ("HP", "EPBCS", BusinessProcessType.PLANNING),
        (None, "Planning Modules", BusinessProcessType.PLANNING),
        ("HP", "FCCS", BusinessProcessType.FCCS),
        (None, "Financial Consolidation and Close", BusinessProcessType.FCCS),
        ("HP", None, BusinessProcessType.UNKNOWN),
        (None, None, BusinessProcessType.UNKNOWN),
    ),
)
def test_business_process_classification_requires_explicit_oracle_metadata(
    product_type: str | None,
    application_type: str | None,
    expected: BusinessProcessType,
) -> None:
    assert (
        classify_business_process(
            product_type=product_type,
            application_type=application_type,
        )
        is expected
    )


def test_product_context_does_not_infer_from_application_name() -> None:
    context = product_context(ApplicationInfo(name="FCCS Production"))

    assert context.application_name == "FCCS Production"
    assert context.business_process is BusinessProcessType.UNKNOWN


def test_registry_rejects_duplicate_product_providers() -> None:
    planning = PRODUCT_PROVIDER_REGISTRY.get(BusinessProcessType.PLANNING)
    assert planning is not None

    with pytest.raises(ValueError, match="already registered"):
        ProductProviderRegistry((planning, planning))
