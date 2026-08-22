"""Contract implemented by Oracle Cloud and future identity adapters."""

from __future__ import annotations

from typing import Protocol

from app.models.identity import (
    IdentityDirectorySnapshot,
    IdentityProviderDefinition,
)


class IdentityDirectoryProvider(Protocol):
    """Retrieve identities without coupling persistence to Oracle APIs."""

    @property
    def definition(self) -> IdentityProviderDefinition:
        """Return safe provider metadata; never return client secrets."""

    def fetch_snapshot(self) -> IdentityDirectorySnapshot:
        """Return one internally consistent directory observation."""

