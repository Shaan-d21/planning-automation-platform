"""Durable provider-neutral identity directory synchronization."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Mapping

from sqlalchemy import delete, func, insert, select, update

from app.identity.provider import IdentityDirectoryProvider
from app.infrastructure.database.engine import (
    DatabaseTarget,
    database_for,
    upsert_statement,
    utc_datetime,
)
from app.infrastructure.database.schema import (
    external_entitlements,
    external_identities,
    external_identity_entitlements,
    identity_providers,
    identity_role_mappings,
    identity_sync_runs,
    platform_roles,
)
from app.models.access_control import RoleCode
from app.models.identity import (
    ExternalEntitlementType,
    ExternalIdentityRecord,
    ExternalEntitlementRecord,
    IdentityDirectorySnapshot,
    IdentityProviderDefinition,
    IdentityProviderRecord,
    IdentityPreviewAction,
    IdentityPreviewEntry,
    IdentitySyncPreview,
    IdentitySyncResult,
    IdentitySyncStatus,
)
from app.utils.exceptions import IdentitySynchronizationError


_PROVIDER_CODE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")
_PROHIBITED_CONFIGURATION_TERMS = (
    "password",
    "secret",
    "token",
    "credential",
    "private_key",
)


class IdentityDirectoryService:
    """Persist identity snapshots while retaining platform audit identity."""

    def __init__(self, database_target: DatabaseTarget) -> None:
        self._database = database_for(database_target)

    def register_provider(
        self,
        definition: IdentityProviderDefinition,
    ) -> IdentityProviderRecord:
        """Register safe provider metadata without storing credentials."""
        code = self._normalize_provider_code(definition.code)
        display_name = self._required(definition.display_name, "display name", 120)
        self._assert_safe_configuration(definition.safe_configuration)
        now = datetime.now(UTC)
        values = {
            "code": code,
            "provider_type": definition.provider_type.value,
            "display_name": display_name,
            "issuer_url": self._optional(definition.issuer_url, 500),
            "environment_key": self._optional(definition.environment_key, 64),
            "is_enabled": definition.enabled,
            "safe_configuration": dict(definition.safe_configuration),
            "created_at": now,
            "updated_at": now,
        }
        with self._database.begin() as connection:
            connection.execute(
                upsert_statement(
                    connection,
                    identity_providers,
                    values,
                    index_elements=("code",),
                    update_columns=(
                        "provider_type",
                        "display_name",
                        "issuer_url",
                        "environment_key",
                        "is_enabled",
                        "safe_configuration",
                        "updated_at",
                    ),
                )
            )
        return self.require_provider(code)

    def sync_from_provider(
        self,
        provider: IdentityDirectoryProvider,
        *,
        initiated_by_user_id: int | None = None,
    ) -> IdentitySyncResult:
        """Fetch and persist one provider snapshot through the common contract."""
        definition = self.register_provider(provider.definition).definition
        try:
            snapshot = provider.fetch_snapshot()
        except Exception as exc:
            raise IdentitySynchronizationError(
                f"Identity provider '{definition.display_name}' could not be read."
            ) from exc
        return self.synchronize(
            definition.code,
            snapshot,
            initiated_by_user_id=initiated_by_user_id,
        )

    def synchronize(
        self,
        provider_code: str,
        snapshot: IdentityDirectorySnapshot,
        *,
        initiated_by_user_id: int | None = None,
    ) -> IdentitySyncResult:
        """Atomically reconcile a complete or partial provider observation."""
        provider = self.require_provider(provider_code)
        if not provider.definition.enabled:
            raise IdentitySynchronizationError(
                f"Identity provider '{provider.definition.code}' is disabled."
            )
        self._validate_snapshot(snapshot)
        sync_run_id = self._start_sync(
            provider.provider_id,
            snapshot,
            initiated_by_user_id,
        )
        try:
            result = self._apply_snapshot(
                sync_run_id,
                provider.provider_id,
                snapshot,
            )
        except Exception as exc:
            self._fail_sync(sync_run_id, exc)
            if isinstance(exc, IdentitySynchronizationError):
                raise
            raise IdentitySynchronizationError(
                f"Identity synchronization for '{provider.definition.code}' failed."
            ) from exc
        return result

    def preview_snapshot(
        self,
        definition: IdentityProviderDefinition,
        snapshot: IdentityDirectorySnapshot,
    ) -> IdentitySyncPreview:
        """Compare a live snapshot with persistence without changing either."""
        self._validate_snapshot(snapshot)
        provider_code = self._normalize_provider_code(definition.code)
        current: dict[str, dict[str, object]] = {}
        with self._database.connect() as connection:
            provider_id = connection.execute(
                select(identity_providers.c.provider_id).where(
                    func.lower(identity_providers.c.code)
                    == provider_code.casefold()
                )
            ).scalar_one_or_none()
            if provider_id is not None:
                rows = connection.execute(
                    select(external_identities).where(
                        external_identities.c.provider_id == int(provider_id)
                    )
                ).mappings().all()
                for row in rows:
                    current[str(row["subject"])] = {
                        "row": row,
                        "entitlements": set(),
                    }
                entitlement_rows = connection.execute(
                    select(
                        external_identities.c.subject,
                        external_entitlements.c.entitlement_type,
                        external_entitlements.c.external_key,
                        external_identity_entitlements.c.assignment_type,
                        external_identity_entitlements.c.granted_through_group,
                    )
                    .select_from(external_identity_entitlements)
                    .join(
                        external_identities,
                        external_identities.c.external_identity_id
                        == external_identity_entitlements.c.external_identity_id,
                    )
                    .join(
                        external_entitlements,
                        external_entitlements.c.entitlement_id
                        == external_identity_entitlements.c.entitlement_id,
                    )
                    .where(external_identities.c.provider_id == int(provider_id))
                ).all()
                for row in entitlement_rows:
                    current[str(row.subject)]["entitlements"].add(
                        (
                            str(row.entitlement_type),
                            str(row.external_key),
                            str(row.assignment_type),
                            str(row.granted_through_group or ""),
                        )
                    )

        entries: list[IdentityPreviewEntry] = []
        observed_subjects: set[str] = set()
        for identity in snapshot.identities:
            observed_subjects.add(identity.subject)
            existing = current.get(identity.subject)
            incoming_entitlements = {
                (
                    item.entitlement_type.value,
                    item.external_key,
                    item.assignment_type.value,
                    item.granted_through_group or "",
                )
                for item in identity.entitlements
            }
            if existing is None:
                action = IdentityPreviewAction.ADD
            else:
                row = existing["row"]
                changed = (
                    str(row["username"]) != identity.username
                    or str(row["display_name"]) != identity.display_name
                    or (str(row["email"]) if row["email"] else None)
                    != identity.email
                    or bool(row["is_active"]) != identity.active
                    or existing["entitlements"] != incoming_entitlements
                )
                action = (
                    IdentityPreviewAction.UPDATE
                    if changed
                    else IdentityPreviewAction.UNCHANGED
                )
            entries.append(self._preview_entry(identity, action))

        if snapshot.complete:
            for subject, existing in current.items():
                row = existing["row"]
                if subject in observed_subjects or not bool(row["is_active"]):
                    continue
                entries.append(
                    IdentityPreviewEntry(
                        subject=subject,
                        username=str(row["username"]),
                        display_name=str(row["display_name"]),
                        email=(str(row["email"]) if row["email"] else None),
                        active=False,
                        action=IdentityPreviewAction.DEACTIVATE,
                    )
                )

        entries.sort(
            key=lambda item: (
                item.action.value,
                item.display_name.casefold(),
                item.username.casefold(),
            )
        )
        warnings = tuple(
            str(item)
            for item in (snapshot.details.get("warnings") or [])
            if str(item).strip()
        )
        return IdentitySyncPreview(
            provider=IdentityProviderDefinition(
                code=provider_code,
                provider_type=definition.provider_type,
                display_name=definition.display_name,
                issuer_url=definition.issuer_url,
                environment_key=definition.environment_key,
                enabled=definition.enabled,
                safe_configuration=dict(definition.safe_configuration),
            ),
            snapshot_checksum=self.snapshot_checksum(snapshot),
            retrieved_at=snapshot.retrieved_at,
            complete=snapshot.complete,
            total_seen=len(snapshot.identities),
            additions=sum(
                item.action == IdentityPreviewAction.ADD for item in entries
            ),
            updates=sum(
                item.action == IdentityPreviewAction.UPDATE for item in entries
            ),
            unchanged=sum(
                item.action == IdentityPreviewAction.UNCHANGED for item in entries
            ),
            deactivations=sum(
                item.action == IdentityPreviewAction.DEACTIVATE for item in entries
            ),
            entries=tuple(entries),
            warnings=warnings,
        )

    @staticmethod
    def snapshot_checksum(snapshot: IdentityDirectorySnapshot) -> str:
        """Return a deterministic checksum excluding retrieval timestamps."""
        canonical = {
            "complete": snapshot.complete,
            "identities": [
                {
                    "subject": identity.subject,
                    "username": identity.username,
                    "display_name": identity.display_name,
                    "email": identity.email,
                    "active": identity.active,
                    "entitlements": [
                        {
                            "type": item.entitlement_type.value,
                            "key": item.external_key,
                            "assignment": item.assignment_type.value,
                            "through": item.granted_through_group,
                        }
                        for item in identity.entitlements
                    ],
                }
                for identity in snapshot.identities
            ],
        }
        serialized = json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def list_providers(self) -> tuple[IdentityProviderRecord, ...]:
        """List safe identity-provider registrations."""
        with self._database.connect() as connection:
            rows = connection.execute(
                select(identity_providers).order_by(
                    func.lower(identity_providers.c.display_name)
                )
            ).mappings().all()
        return tuple(self._provider_from_row(row) for row in rows)

    def require_provider(self, code: str) -> IdentityProviderRecord:
        """Resolve a provider by stable, case-insensitive code."""
        normalized = self._normalize_provider_code(code)
        with self._database.connect() as connection:
            row = connection.execute(
                select(identity_providers).where(
                    func.lower(identity_providers.c.code) == normalized.casefold()
                )
            ).mappings().one_or_none()
        if row is None:
            raise IdentitySynchronizationError(
                f"Identity provider '{normalized}' is not registered."
            )
        return self._provider_from_row(row)

    def list_external_identities(
        self,
        provider_code: str,
        *,
        include_inactive: bool = False,
    ) -> tuple[ExternalIdentityRecord, ...]:
        """List synchronized identities without exposing authentication data."""
        provider = self.require_provider(provider_code)
        statement = select(external_identities).where(
            external_identities.c.provider_id == provider.provider_id
        )
        if not include_inactive:
            statement = statement.where(external_identities.c.is_active.is_(True))
        statement = statement.order_by(
            func.lower(external_identities.c.display_name),
            func.lower(external_identities.c.username),
        )
        with self._database.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(
            ExternalIdentityRecord(
                external_identity_id=int(row["external_identity_id"]),
                provider_code=provider.definition.code,
                user_id=(int(row["user_id"]) if row["user_id"] is not None else None),
                subject=str(row["subject"]),
                username=str(row["username"]),
                display_name=str(row["display_name"]),
                email=(str(row["email"]) if row["email"] else None),
                active=bool(row["is_active"]),
                last_seen_at=utc_datetime(row["last_seen_at"]),
                last_synced_at=utc_datetime(row["last_synced_at"]),
            )
            for row in rows
        )

    def list_entitlements(
        self,
        provider_code: str,
        *,
        include_inactive: bool = False,
    ) -> tuple[ExternalEntitlementRecord, ...]:
        """List synchronized roles/groups and their explicit platform mapping."""
        provider = self.require_provider(provider_code)
        assignment_counts = (
            select(
                external_identity_entitlements.c.entitlement_id.label(
                    "entitlement_id"
                ),
                func.count().label("assigned_identity_count"),
            )
            .group_by(external_identity_entitlements.c.entitlement_id)
            .subquery()
        )
        statement = (
            select(
                external_entitlements,
                platform_roles.c.code.label("mapped_role"),
                identity_role_mappings.c.is_enabled.label("mapping_enabled"),
                func.coalesce(
                    assignment_counts.c.assigned_identity_count,
                    0,
                ).label("assigned_identity_count"),
            )
            .outerjoin(
                identity_role_mappings,
                identity_role_mappings.c.entitlement_id
                == external_entitlements.c.entitlement_id,
            )
            .outerjoin(
                platform_roles,
                platform_roles.c.role_id == identity_role_mappings.c.role_id,
            )
            .outerjoin(
                assignment_counts,
                assignment_counts.c.entitlement_id
                == external_entitlements.c.entitlement_id,
            )
            .where(external_entitlements.c.provider_id == provider.provider_id)
        )
        if not include_inactive:
            statement = statement.where(external_entitlements.c.is_active.is_(True))
        statement = statement.order_by(
            external_entitlements.c.entitlement_type,
            func.lower(external_entitlements.c.display_name),
        )
        with self._database.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(
            ExternalEntitlementRecord(
                entitlement_id=int(row["entitlement_id"]),
                provider_code=provider.definition.code,
                entitlement_type=ExternalEntitlementType(
                    str(row["entitlement_type"])
                ),
                external_key=str(row["external_key"]),
                display_name=str(row["display_name"]),
                active=bool(row["is_active"]),
                assigned_identity_count=int(row["assigned_identity_count"]),
                mapped_role=(
                    str(row["mapped_role"]) if row["mapped_role"] else None
                ),
                mapping_enabled=bool(row["mapping_enabled"]),
            )
            for row in rows
        )

    def set_role_mapping(
        self,
        provider_code: str,
        entitlement_id: int,
        role: RoleCode,
        *,
        actor_user_id: int,
    ) -> ExternalEntitlementRecord:
        """Map one live Oracle entitlement to one platform role explicitly."""
        provider = self.require_provider(provider_code)
        normalized_role = RoleCode(role)
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(
                    select(
                        func.pg_advisory_xact_lock(
                            9_100_000 + provider.provider_id
                        )
                    )
                )
            entitlement = connection.execute(
                select(external_entitlements).where(
                    external_entitlements.c.entitlement_id == entitlement_id,
                    external_entitlements.c.provider_id == provider.provider_id,
                    external_entitlements.c.is_active.is_(True),
                )
            ).mappings().one_or_none()
            if entitlement is None:
                raise IdentitySynchronizationError(
                    "The selected Oracle entitlement is not active in this "
                    "identity provider."
                )
            role_id = connection.execute(
                select(platform_roles.c.role_id).where(
                    platform_roles.c.code == normalized_role.value
                )
            ).scalar_one_or_none()
            if role_id is None:
                raise IdentitySynchronizationError(
                    f"Platform role '{normalized_role.value}' was not found."
                )
            connection.execute(
                upsert_statement(
                    connection,
                    identity_role_mappings,
                    {
                        "provider_id": provider.provider_id,
                        "entitlement_id": entitlement_id,
                        "role_id": int(role_id),
                        "is_enabled": True,
                        "created_by_user_id": actor_user_id,
                        "created_at": now,
                        "updated_at": now,
                    },
                    index_elements=("entitlement_id",),
                    update_columns=(
                        "provider_id",
                        "role_id",
                        "is_enabled",
                        "updated_at",
                    ),
                )
            )
        return next(
            item
            for item in self.list_entitlements(
                provider.definition.code,
                include_inactive=True,
            )
            if item.entitlement_id == entitlement_id
        )

    def remove_role_mapping(
        self,
        provider_code: str,
        entitlement_id: int,
    ) -> None:
        """Remove one mapping without modifying Oracle or linked users yet."""
        provider = self.require_provider(provider_code)
        with self._database.begin() as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(
                    select(
                        func.pg_advisory_xact_lock(
                            9_100_000 + provider.provider_id
                        )
                    )
                )
            result = connection.execute(
                delete(identity_role_mappings).where(
                    identity_role_mappings.c.provider_id == provider.provider_id,
                    identity_role_mappings.c.entitlement_id == entitlement_id,
                )
            )
        if not result.rowcount:
            raise IdentitySynchronizationError(
                "The selected identity role mapping was not found."
            )

    def _apply_snapshot(
        self,
        sync_run_id: int,
        provider_id: int,
        snapshot: IdentityDirectorySnapshot,
    ) -> IdentitySyncResult:
        observed_subjects: set[str] = set()
        observed_entitlements: set[tuple[str, str]] = set()
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(
                    select(func.pg_advisory_xact_lock(9_100_000 + provider_id))
                )
            for identity in snapshot.identities:
                subject = self._required(identity.subject, "identity subject", 255)
                observed_subjects.add(subject)
                identity_values = {
                    "provider_id": provider_id,
                    "user_id": None,
                    "subject": subject,
                    "username": self._required(identity.username, "username", 254),
                    "display_name": self._required(
                        identity.display_name,
                        "display name",
                        200,
                    ),
                    "email": self._optional(identity.email, 254),
                    "is_active": identity.active,
                    "last_seen_at": snapshot.retrieved_at,
                    "last_synced_at": snapshot.retrieved_at,
                    "created_at": now,
                    "updated_at": now,
                }
                connection.execute(
                    upsert_statement(
                        connection,
                        external_identities,
                        identity_values,
                        index_elements=("provider_id", "subject"),
                        update_columns=(
                            "username",
                            "display_name",
                            "email",
                            "is_active",
                            "last_seen_at",
                            "last_synced_at",
                            "updated_at",
                        ),
                    )
                )
                external_identity_id = int(
                    connection.execute(
                        select(external_identities.c.external_identity_id).where(
                            external_identities.c.provider_id == provider_id,
                            external_identities.c.subject == subject,
                        )
                    ).scalar_one()
                )
                connection.execute(
                    delete(external_identity_entitlements).where(
                        external_identity_entitlements.c.external_identity_id
                        == external_identity_id
                    )
                )
                assigned_entitlement_ids: set[int] = set()
                for entitlement in identity.entitlements:
                    entitlement_type = entitlement.entitlement_type.value
                    external_key = self._required(
                        entitlement.external_key,
                        "entitlement key",
                        255,
                    )
                    observed_entitlements.add((entitlement_type, external_key))
                    entitlement_values = {
                        "provider_id": provider_id,
                        "entitlement_type": entitlement_type,
                        "external_key": external_key,
                        "display_name": self._required(
                            entitlement.display_name,
                            "entitlement display name",
                            255,
                        ),
                        "is_active": True,
                        "created_at": now,
                        "updated_at": now,
                    }
                    connection.execute(
                        upsert_statement(
                            connection,
                            external_entitlements,
                            entitlement_values,
                            index_elements=(
                                "provider_id",
                                "entitlement_type",
                                "external_key",
                            ),
                            update_columns=("display_name", "is_active", "updated_at"),
                        )
                    )
                    entitlement_id = int(
                        connection.execute(
                            select(external_entitlements.c.entitlement_id).where(
                                external_entitlements.c.provider_id == provider_id,
                                external_entitlements.c.entitlement_type
                                == entitlement_type,
                                external_entitlements.c.external_key == external_key,
                            )
                        ).scalar_one()
                    )
                    if entitlement_id in assigned_entitlement_ids:
                        continue
                    assigned_entitlement_ids.add(entitlement_id)
                    connection.execute(
                        insert(external_identity_entitlements).values(
                            external_identity_id=external_identity_id,
                            entitlement_id=entitlement_id,
                            assignment_type=entitlement.assignment_type.value,
                            granted_through_group=self._optional(
                                entitlement.granted_through_group,
                                255,
                            ),
                            observed_at=snapshot.retrieved_at,
                        )
                    )

            deactivated = 0
            if snapshot.complete:
                missing_identity_filter = [
                    external_identities.c.provider_id == provider_id,
                    external_identities.c.is_active.is_(True),
                ]
                if observed_subjects:
                    missing_identity_filter.append(
                        external_identities.c.subject.not_in(observed_subjects)
                    )
                result = connection.execute(
                    update(external_identities)
                    .where(*missing_identity_filter)
                    .values(is_active=False, last_synced_at=now, updated_at=now)
                )
                deactivated = int(result.rowcount or 0)
                entitlement_rows = connection.execute(
                    select(
                        external_entitlements.c.entitlement_id,
                        external_entitlements.c.entitlement_type,
                        external_entitlements.c.external_key,
                    ).where(external_entitlements.c.provider_id == provider_id)
                ).all()
                for entitlement_id, entitlement_type, external_key in entitlement_rows:
                    active = (str(entitlement_type), str(external_key)) in observed_entitlements
                    connection.execute(
                        update(external_entitlements)
                        .where(external_entitlements.c.entitlement_id == entitlement_id)
                        .values(is_active=active, updated_at=now)
                    )

            linked = int(
                connection.execute(
                    select(func.count())
                    .select_from(external_identities)
                    .where(
                        external_identities.c.provider_id == provider_id,
                        external_identities.c.subject.in_(observed_subjects)
                        if observed_subjects
                        else external_identities.c.external_identity_id.is_(None),
                        external_identities.c.user_id.is_not(None),
                    )
                ).scalar_one()
            )
            status = (
                IdentitySyncStatus.SUCCESS
                if snapshot.complete
                else IdentitySyncStatus.PARTIAL
            )
            connection.execute(
                update(identity_sync_runs)
                .where(identity_sync_runs.c.sync_run_id == sync_run_id)
                .values(
                    status=status.value,
                    completed_at=now,
                    identities_seen=len(snapshot.identities),
                    identities_linked=linked,
                    identities_deactivated=deactivated,
                    entitlements_seen=len(observed_entitlements),
                )
            )
        return IdentitySyncResult(
            sync_run_id=sync_run_id,
            status=status,
            identities_seen=len(snapshot.identities),
            identities_linked=linked,
            identities_deactivated=deactivated,
            entitlements_seen=len(observed_entitlements),
            completed_at=now,
        )

    def _start_sync(
        self,
        provider_id: int,
        snapshot: IdentityDirectorySnapshot,
        initiated_by_user_id: int | None,
    ) -> int:
        with self._database.begin() as connection:
            return int(
                connection.execute(
                    insert(identity_sync_runs)
                    .values(
                        provider_id=provider_id,
                        status=IdentitySyncStatus.RUNNING.value,
                        started_at=datetime.now(UTC),
                        initiated_by_user_id=initiated_by_user_id,
                        details=dict(snapshot.details),
                    )
                    .returning(identity_sync_runs.c.sync_run_id)
                ).scalar_one()
            )

    @staticmethod
    def _preview_entry(identity, action: IdentityPreviewAction) -> IdentityPreviewEntry:
        return IdentityPreviewEntry(
            subject=identity.subject,
            username=identity.username,
            display_name=identity.display_name,
            email=identity.email,
            active=identity.active,
            action=action,
            application_roles=tuple(
                sorted(
                    item.display_name
                    for item in identity.entitlements
                    if item.entitlement_type.value == "APPLICATION_ROLE"
                )
            ),
            granular_roles=tuple(
                sorted(
                    item.display_name
                    for item in identity.entitlements
                    if item.entitlement_type.value == "GRANULAR_ROLE"
                )
            ),
            groups=tuple(
                sorted(
                    item.display_name
                    for item in identity.entitlements
                    if item.entitlement_type.value == "GROUP"
                )
            ),
        )

    def _fail_sync(self, sync_run_id: int, error: Exception) -> None:
        summary = str(error).strip() or error.__class__.__name__
        with self._database.begin() as connection:
            connection.execute(
                update(identity_sync_runs)
                .where(identity_sync_runs.c.sync_run_id == sync_run_id)
                .values(
                    status=IdentitySyncStatus.FAILED.value,
                    completed_at=datetime.now(UTC),
                    error_summary=summary[:1_000],
                )
            )

    @staticmethod
    def _validate_snapshot(snapshot: IdentityDirectorySnapshot) -> None:
        if snapshot.retrieved_at.tzinfo is None:
            raise IdentitySynchronizationError(
                "Identity snapshot timestamp must include a timezone."
            )
        subjects = [identity.subject.strip() for identity in snapshot.identities]
        if len(subjects) != len(set(subjects)):
            raise IdentitySynchronizationError(
                "Identity snapshot contains duplicate provider subjects."
            )

    @staticmethod
    def _normalize_provider_code(value: str) -> str:
        code = str(value).strip()
        if not _PROVIDER_CODE_PATTERN.fullmatch(code):
            raise IdentitySynchronizationError(
                "Identity provider code must contain 2-64 letters, numbers, "
                "underscores, or hyphens."
            )
        return code.casefold()

    @staticmethod
    def _required(value: str, label: str, maximum: int) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise IdentitySynchronizationError(f"Identity {label} is required.")
        if len(normalized) > maximum:
            raise IdentitySynchronizationError(
                f"Identity {label} cannot exceed {maximum} characters."
            )
        return normalized

    @staticmethod
    def _optional(value: str | None, maximum: int) -> str | None:
        normalized = str(value).strip() if value is not None else ""
        if not normalized:
            return None
        if len(normalized) > maximum:
            raise IdentitySynchronizationError(
                f"Identity value cannot exceed {maximum} characters."
            )
        return normalized

    @classmethod
    def _assert_safe_configuration(
        cls,
        configuration: Mapping[str, object],
        *,
        path: str = "configuration",
    ) -> None:
        for key, value in configuration.items():
            normalized_key = str(key).casefold()
            if any(term in normalized_key for term in _PROHIBITED_CONFIGURATION_TERMS):
                raise IdentitySynchronizationError(
                    f"{path}.{key} appears to contain secret material and cannot "
                    "be persisted."
                )
            if isinstance(value, Mapping):
                cls._assert_safe_configuration(value, path=f"{path}.{key}")

    @staticmethod
    def _provider_from_row(row) -> IdentityProviderRecord:
        from app.models.identity import IdentityProviderType

        return IdentityProviderRecord(
            provider_id=int(row["provider_id"]),
            definition=IdentityProviderDefinition(
                code=str(row["code"]),
                provider_type=IdentityProviderType(str(row["provider_type"])),
                display_name=str(row["display_name"]),
                issuer_url=(str(row["issuer_url"]) if row["issuer_url"] else None),
                environment_key=(
                    str(row["environment_key"]) if row["environment_key"] else None
                ),
                enabled=bool(row["is_enabled"]),
                safe_configuration=dict(row["safe_configuration"] or {}),
            ),
            created_at=utc_datetime(row["created_at"]),
            updated_at=utc_datetime(row["updated_at"]),
        )
