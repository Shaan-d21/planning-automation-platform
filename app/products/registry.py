"""Allow-listed registry of Oracle EPM product providers."""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import Mapping

from app.products.contracts import (
    BusinessProcessType,
    CapabilityDefinition,
    EPMProductProvider,
    NavigationDefinition,
    OperationDefinition,
)
from app.products.common import (
    COMMON_EPM_CAPABILITIES,
    COMMON_NAVIGATION_DEFINITIONS,
)
from app.products.fccs.provider import FCCSProductProvider
from app.products.planning.provider import PlanningProductProvider


class ProductProviderRegistry:
    """Resolve providers by verified business-process type."""

    def __init__(self, providers: Iterable[EPMProductProvider]) -> None:
        registered: dict[BusinessProcessType, EPMProductProvider] = {}
        for provider in providers:
            if provider.business_process is BusinessProcessType.UNKNOWN:
                raise ValueError("UNKNOWN cannot register product operations.")
            if provider.business_process in registered:
                raise ValueError(
                    "A provider is already registered for "
                    f"{provider.business_process.value}."
                )
            registered[provider.business_process] = provider
        self._providers: Mapping[
            BusinessProcessType, EPMProductProvider
        ] = MappingProxyType(registered)

    def get(
        self,
        business_process: BusinessProcessType,
        *,
        include_disabled: bool = False,
    ) -> EPMProductProvider | None:
        provider = self._providers.get(business_process)
        if provider is None or (not provider.enabled and not include_disabled):
            return None
        return provider

    def operations_for(
        self,
        business_process: BusinessProcessType,
    ) -> tuple[OperationDefinition, ...]:
        provider = self.get(business_process)
        return provider.operations() if provider is not None else ()

    def navigation_for(
        self,
        business_process: BusinessProcessType,
    ) -> tuple[NavigationDefinition, ...]:
        """Compose shared shell entries with enabled product navigation."""

        provider = self.get(business_process)
        product_navigation = provider.navigation() if provider is not None else ()
        return tuple(
            sorted(
                COMMON_NAVIGATION_DEFINITIONS + product_navigation,
                key=lambda item: item.order,
            )
        )

    def capabilities_for(
        self,
        business_process: BusinessProcessType,
    ) -> tuple[CapabilityDefinition, ...]:
        """Compose common EPM and enabled product-specific capabilities."""

        provider = self.get(business_process)
        product_capabilities = provider.capabilities() if provider is not None else ()
        return COMMON_EPM_CAPABILITIES + product_capabilities

    def agent_tool_names_for(
        self,
        business_process: BusinessProcessType,
    ) -> frozenset[str]:
        """Return the provider-composed agent tool allow-list."""

        return frozenset(
            tool_name
            for capability in self.capabilities_for(business_process)
            for tool_name in capability.agent_tools
        )


PRODUCT_PROVIDER_REGISTRY = ProductProviderRegistry(
    (PlanningProductProvider(), FCCSProductProvider())
)
