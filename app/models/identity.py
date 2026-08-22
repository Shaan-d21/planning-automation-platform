"""Provider-neutral identity synchronization models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class IdentityProviderType(StrEnum):
    LOCAL = "LOCAL"
    ORACLE_CLOUD = "ORACLE_CLOUD"
    OIDC = "OIDC"
    LDAP = "LDAP"


class ExternalEntitlementType(StrEnum):
    APPLICATION_ROLE = "APPLICATION_ROLE"
    GRANULAR_ROLE = "GRANULAR_ROLE"
    GROUP = "GROUP"


class ExternalAssignmentType(StrEnum):
    DIRECT = "DIRECT"
    INHERITED = "INHERITED"


class IdentitySyncStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class IdentityProviderDefinition:
    """Safe, non-secret configuration for one identity authority."""

    code: str
    provider_type: IdentityProviderType
    display_name: str
    issuer_url: str | None = None
    environment_key: str | None = None
    enabled: bool = True
    safe_configuration: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExternalEntitlementSnapshot:
    """One role or group assignment observed at the provider."""

    external_key: str
    display_name: str
    entitlement_type: ExternalEntitlementType
    assignment_type: ExternalAssignmentType = ExternalAssignmentType.DIRECT
    granted_through_group: str | None = None


@dataclass(frozen=True, slots=True)
class ExternalIdentitySnapshot:
    """One provider identity and its complete observed entitlements."""

    subject: str
    username: str
    display_name: str
    email: str | None = None
    active: bool = True
    entitlements: tuple[ExternalEntitlementSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class IdentityDirectorySnapshot:
    """An atomic directory observation returned by a provider adapter."""

    identities: tuple[ExternalIdentitySnapshot, ...]
    retrieved_at: datetime
    complete: bool = True
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IdentitySyncResult:
    """Persisted outcome of one directory synchronization."""

    sync_run_id: int
    status: IdentitySyncStatus
    identities_seen: int
    identities_linked: int
    identities_deactivated: int
    entitlements_seen: int
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class IdentityProviderRecord:
    """Persisted identity-provider metadata suitable for administration UI."""

    provider_id: int
    definition: IdentityProviderDefinition
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ExternalIdentityRecord:
    """Persisted provider identity, optionally linked to a platform user."""

    external_identity_id: int
    provider_code: str
    user_id: int | None
    subject: str
    username: str
    display_name: str
    email: str | None
    active: bool
    last_seen_at: datetime | None
    last_synced_at: datetime


class IdentityPreviewAction(StrEnum):
    ADD = "ADD"
    UPDATE = "UPDATE"
    UNCHANGED = "UNCHANGED"
    DEACTIVATE = "DEACTIVATE"


@dataclass(frozen=True, slots=True)
class IdentityPreviewEntry:
    """One proposed directory change with no platform-role side effects."""

    subject: str
    username: str
    display_name: str
    email: str | None
    active: bool
    action: IdentityPreviewAction
    application_roles: tuple[str, ...] = ()
    granular_roles: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IdentitySyncPreview:
    """Read-only reconciliation plan presented before synchronization."""

    provider: IdentityProviderDefinition
    snapshot_checksum: str
    retrieved_at: datetime
    complete: bool
    total_seen: int
    additions: int
    updates: int
    unchanged: int
    deactivations: int
    entries: tuple[IdentityPreviewEntry, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExternalEntitlementRecord:
    """One synchronized Oracle role or group and its local mapping."""

    entitlement_id: int
    provider_code: str
    entitlement_type: ExternalEntitlementType
    external_key: str
    display_name: str
    active: bool
    assigned_identity_count: int
    mapped_role: str | None = None
    mapping_enabled: bool = False


class IdentityProvisioningAction(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    UNCHANGED = "UNCHANGED"
    DEACTIVATE = "DEACTIVATE"
    SKIP_UNMAPPED = "SKIP_UNMAPPED"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class IdentityProvisioningEntry:
    """One proposed shadow-account outcome derived from approved mappings."""

    external_identity_id: int
    user_id: int | None
    username: str
    display_name: str
    email: str | None
    target_role: str | None
    action: IdentityProvisioningAction
    matched_entitlements: tuple[str, ...] = ()
    explanation: str = ""


@dataclass(frozen=True, slots=True)
class IdentityProvisioningPreview:
    """Checksum-protected shadow-account provisioning plan."""

    provider_code: str
    checksum: str
    generated_at: datetime
    entries: tuple[IdentityProvisioningEntry, ...]
    creates: int
    updates: int
    unchanged: int
    deactivations: int
    unmapped: int
    conflicts: int


@dataclass(frozen=True, slots=True)
class IdentityProvisioningResult:
    """Outcome of applying safe entries from a reviewed provisioning plan."""

    created: int
    updated: int
    deactivated: int
    unchanged: int
    unmapped: int
    conflicts: int
