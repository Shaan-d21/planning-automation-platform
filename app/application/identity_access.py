"""Preview-first Oracle identity synchronization application service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Protocol

from app.clients.epm_client import EPMClient
from app.config.settings import Settings
from app.identity.oracle_epm_provider import OracleEPMIdentityProvider
from app.models.access_control import RoleCode
from app.models.identity import (
    IdentityProvisioningPreview,
    IdentitySyncPreview,
    IdentitySyncResult,
)
from app.services.federated_provisioning_service import (
    FederatedProvisioningService,
)
from app.services.identity_directory_service import IdentityDirectoryService
from app.utils.exceptions import (
    IdentitySnapshotChangedError,
    IdentitySynchronizationError,
)


class ClosableIdentityClient(Protocol):
    def get(self, endpoint: str, *, params=None): ...

    def post(self, endpoint: str, *, payload=None, params=None): ...

    def close(self) -> None: ...


class IdentityAccessApplicationService:
    """Coordinate live Oracle discovery and governed local synchronization."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[Settings], ClosableIdentityClient] | None = None,
    ) -> None:
        self._settings = settings
        self._directory = IdentityDirectoryService(settings.database_target)
        self._provisioning = FederatedProvisioningService(
            settings.database_target
        )
        self._client_factory = client_factory or EPMClient

    def status(self) -> dict[str, object]:
        """Return safe configuration and retained-directory status."""
        definition = OracleEPMIdentityProvider.definition_for(self._settings)
        providers = {
            item.definition.code: item
            for item in self._directory.list_providers()
        }
        provider = providers.get(definition.code)
        identities = (
            self._directory.list_external_identities(
                definition.code,
                include_inactive=True,
            )
            if provider is not None
            else ()
        )
        return {
            "available": self._settings.resolved_deployment_mode == "cloud",
            "provider_code": definition.code,
            "provider_name": definition.display_name,
            "provider_registered": provider is not None,
            "identity_provider_mode": self._settings.identity_provider,
            "sso_enabled": self._settings.federated_identity_ready,
            "oracle_password_login_enabled": (
                self._settings.oracle_password_login_ready
            ),
            "synced_identities": len(identities),
            "active_identities": sum(item.active for item in identities),
            "mapped_entitlements": (
                sum(
                    item.mapping_enabled
                    for item in self._directory.list_entitlements(
                        definition.code,
                        include_inactive=True,
                    )
                )
                if provider is not None
                else 0
            ),
            "message": (
                "Ready to inspect Oracle Cloud EPM Access Control."
                if self._settings.resolved_deployment_mode == "cloud"
                else "Connect an Oracle Cloud EPM environment to synchronize access."
            ),
        }

    def entitlement_catalog(self) -> dict[str, object]:
        """Return synchronized Oracle entitlements and explicit mappings."""
        definition = OracleEPMIdentityProvider.definition_for(self._settings)
        entitlements = self._directory.list_entitlements(
            definition.code,
            include_inactive=True,
        )
        return {
            "provider_code": definition.code,
            "entitlements": [
                {
                    "entitlement_id": item.entitlement_id,
                    "entitlement_type": item.entitlement_type.value,
                    "external_key": item.external_key,
                    "display_name": item.display_name,
                    "active": item.active,
                    "assigned_identity_count": item.assigned_identity_count,
                    "mapped_role": item.mapped_role,
                    "mapping_enabled": item.mapping_enabled,
                }
                for item in entitlements
            ],
        }

    def set_mapping(
        self,
        entitlement_id: int,
        role_code: RoleCode,
        *,
        actor_user_id: int,
    ) -> dict[str, object]:
        definition = OracleEPMIdentityProvider.definition_for(self._settings)
        item = self._directory.set_role_mapping(
            definition.code,
            entitlement_id,
            role_code,
            actor_user_id=actor_user_id,
        )
        return {
            "entitlement_id": item.entitlement_id,
            "mapped_role": item.mapped_role,
            "mapping_enabled": item.mapping_enabled,
        }

    def remove_mapping(self, entitlement_id: int) -> None:
        definition = OracleEPMIdentityProvider.definition_for(self._settings)
        self._directory.remove_role_mapping(definition.code, entitlement_id)

    def provisioning_preview(self) -> IdentityProvisioningPreview:
        definition = OracleEPMIdentityProvider.definition_for(self._settings)
        return self._provisioning.preview(definition.code)

    def provision(
        self,
        expected_checksum: str,
        *,
        actor_user_id: int,
    ):
        definition = OracleEPMIdentityProvider.definition_for(self._settings)
        return self._provisioning.apply(
            definition.code,
            expected_checksum,
            actor_user_id=actor_user_id,
        )

    def preview(self) -> IdentitySyncPreview:
        """Inspect Oracle and return a local, read-only reconciliation plan."""
        provider, snapshot = self._fetch()
        return self._directory.preview_snapshot(provider.definition, snapshot)

    def synchronize(
        self,
        expected_checksum: str,
        *,
        initiated_by_user_id: int,
    ) -> tuple[IdentitySyncResult, IdentitySyncPreview]:
        """Refetch Oracle, reject drift, then persist external identities only."""
        checksum = str(expected_checksum).strip().casefold()
        if len(checksum) != 64 or any(
            character not in "0123456789abcdef" for character in checksum
        ):
            raise IdentitySynchronizationError(
                "A valid identity preview checksum is required."
            )
        provider, snapshot = self._fetch()
        preview = self._directory.preview_snapshot(provider.definition, snapshot)
        if preview.snapshot_checksum != checksum:
            raise IdentitySnapshotChangedError(
                "Oracle access changed after the preview. Review the refreshed "
                "changes before synchronizing."
            )
        self._directory.register_provider(provider.definition)
        result = self._directory.synchronize(
            provider.definition.code,
            snapshot,
            initiated_by_user_id=initiated_by_user_id,
        )
        return result, preview

    def _fetch(self):
        client = self._client_factory(self._settings)
        try:
            provider = OracleEPMIdentityProvider(self._settings, client)
            snapshot = provider.fetch_snapshot()
            return provider, snapshot
        finally:
            client.close()


def identity_preview_payload(preview: IdentitySyncPreview) -> dict[str, object]:
    """Serialize a preview while retaining stable field names for the frontend."""
    return {
        "provider": {
            "code": preview.provider.code,
            "display_name": preview.provider.display_name,
            "provider_type": preview.provider.provider_type.value,
        },
        "snapshot_checksum": preview.snapshot_checksum,
        "retrieved_at": preview.retrieved_at.isoformat(),
        "complete": preview.complete,
        "summary": {
            "total_seen": preview.total_seen,
            "additions": preview.additions,
            "updates": preview.updates,
            "unchanged": preview.unchanged,
            "deactivations": preview.deactivations,
        },
        "warnings": list(preview.warnings),
        "identities": [
            {
                **asdict(entry),
                "action": entry.action.value,
            }
            for entry in preview.entries
        ],
    }


def provisioning_preview_payload(
    preview: IdentityProvisioningPreview,
) -> dict[str, object]:
    """Serialize a governed linked-profile provisioning preview."""
    return {
        "provider_code": preview.provider_code,
        "checksum": preview.checksum,
        "generated_at": preview.generated_at.isoformat(),
        "summary": {
            "creates": preview.creates,
            "updates": preview.updates,
            "unchanged": preview.unchanged,
            "deactivations": preview.deactivations,
            "unmapped": preview.unmapped,
            "conflicts": preview.conflicts,
        },
        "entries": [
            {
                "external_identity_id": entry.external_identity_id,
                "user_id": entry.user_id,
                "username": entry.username,
                "display_name": entry.display_name,
                "email": entry.email,
                "target_role": entry.target_role,
                "action": entry.action.value,
                "matched_entitlements": list(entry.matched_entitlements),
                "explanation": entry.explanation,
            }
            for entry in preview.entries
        ],
    }
