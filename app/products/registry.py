"""Allow-listed registry of Oracle EPM product providers."""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import Mapping

from app.products.contracts import (
    BusinessProcessType,
    EPMProductProvider,
    OperationDefinition,
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


PRODUCT_PROVIDER_REGISTRY = ProductProviderRegistry(
    (PlanningProductProvider(), FCCSProductProvider())
)
