"""Disabled FCCS provider reserved for the verified read-only foundation."""

from __future__ import annotations

from app.products.contracts import (
    BusinessProcessType,
    CapabilityDefinition,
    NavigationDefinition,
    OperationDefinition,
)


class FCCSProductProvider:
    """Declare FCCS without exposing unimplemented Oracle operations."""

    business_process = BusinessProcessType.FCCS
    enabled = False

    def operations(self) -> tuple[OperationDefinition, ...]:
        return ()

    def navigation(self) -> tuple[NavigationDefinition, ...]:
        return ()

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return ()
