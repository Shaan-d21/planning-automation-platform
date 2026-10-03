"""Stable contracts shared by Oracle EPM business-process modules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class BusinessProcessType(StrEnum):
    """Business processes that may contribute platform capabilities."""

    PLANNING = "PLANNING"
    FCCS = "FCCS"
    UNKNOWN = "UNKNOWN"


class OperationKind(StrEnum):
    """Stable standalone operation identifiers persisted by the platform."""

    BUSINESS_RULE = "BUSINESS_RULE"
    DATA_MAP = "DATA_MAP"
    PIPELINE = "PIPELINE"
    DATA_INTEGRATION = "DATA_INTEGRATION"
    METADATA_IMPORT = "METADATA_IMPORT"
    DATA_IMPORT = "DATA_IMPORT"
    SUBSTITUTION_VARIABLE = "SUBSTITUTION_VARIABLE"
    USER_VARIABLE = "USER_VARIABLE"
    CUBE_REFRESH = "CUBE_REFRESH"
    REPORT_GENERATION = "REPORT_GENERATION"
    STANDALONE_FLOW = "STANDALONE_FLOW"


@dataclass(frozen=True, slots=True)
class OperationDefinition:
    """Presentation and governance metadata for one operation."""

    kind: OperationKind
    code: str
    display_name: str
    description: str
    category: str
    risk_level: str
    route: str


@dataclass(frozen=True, slots=True)
class ProductContext:
    """Verified, non-secret identity of the active Oracle application."""

    business_process: BusinessProcessType
    application_name: str
    product_type: str | None = None
    application_type: str | None = None


@runtime_checkable
class EPMProductProvider(Protocol):
    """Extension point through which one EPM product contributes behavior."""

    @property
    def business_process(self) -> BusinessProcessType:
        """Return the provider's stable business-process identifier."""

    @property
    def enabled(self) -> bool:
        """Return whether this product can currently expose operations."""

    def operations(self) -> tuple[OperationDefinition, ...]:
        """Return the governed operations contributed by this product."""
