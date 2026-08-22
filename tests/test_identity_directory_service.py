"""Provider-neutral identity persistence and compatibility tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert, select

from app.infrastructure.database.engine import database_for
from app.infrastructure.database.schema import (
    external_entitlements,
    external_identities,
    external_identity_entitlements,
    identity_sync_runs,
    platform_users,
)
from app.models.identity import (
    ExternalAssignmentType,
    ExternalEntitlementSnapshot,
    ExternalEntitlementType,
    ExternalIdentitySnapshot,
    IdentityDirectorySnapshot,
    IdentityProviderDefinition,
    IdentityProviderType,
    IdentityPreviewAction,
    IdentitySyncStatus,
)
from app.services.access_control_service import AccessControlService
from app.services.identity_directory_service import IdentityDirectoryService
from app.utils.exceptions import IdentitySynchronizationError


def _provider() -> IdentityProviderDefinition:
    return IdentityProviderDefinition(
        code="oracle-cloud-main",
        provider_type=IdentityProviderType.ORACLE_CLOUD,
        display_name="Oracle Cloud Identity",
        issuer_url="https://identity.example.com",
        environment_key="production",
        safe_configuration={"tenant": "example"},
    )


def _snapshot(*, include_second_user: bool = True) -> IdentityDirectorySnapshot:
    planner = ExternalEntitlementSnapshot(
        external_key="Planning User",
        display_name="Planning User",
        entitlement_type=ExternalEntitlementType.APPLICATION_ROLE,
    )
    finance = ExternalEntitlementSnapshot(
        external_key="Finance Planners",
        display_name="Finance Planners",
        entitlement_type=ExternalEntitlementType.GROUP,
        assignment_type=ExternalAssignmentType.INHERITED,
        granted_through_group="Finance",
    )
    identities = [
        ExternalIdentitySnapshot(
            subject="ocid-user-1",
            username="planner@example.com",
            display_name="Planning User",
            email="planner@example.com",
            entitlements=(planner, finance),
        )
    ]
    if include_second_user:
        identities.append(
            ExternalIdentitySnapshot(
                subject="ocid-user-2",
                username="viewer@example.com",
                display_name="Planning Viewer",
                email="viewer@example.com",
                entitlements=(planner,),
            )
        )
    return IdentityDirectorySnapshot(
        identities=tuple(identities),
        retrieved_at=datetime.now(UTC),
        details={"source": "test-adapter"},
    )


def test_synchronization_persists_identities_entitlements_and_audit(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "identity.sqlite3"
    service = IdentityDirectoryService(database_path)
    service.register_provider(_provider())

    result = service.synchronize("ORACLE-CLOUD-MAIN", _snapshot())

    assert result.status == IdentitySyncStatus.SUCCESS
    assert result.identities_seen == 2
    assert result.entitlements_seen == 2
    assert [item.username for item in service.list_external_identities(
        "oracle-cloud-main"
    )] == ["planner@example.com", "viewer@example.com"]
    database = database_for(database_path)
    with database.connect() as connection:
        assert connection.execute(
            select(external_entitlements.c.entitlement_id)
        ).all()
        assert len(connection.execute(
            select(external_identity_entitlements)
        ).all()) == 3
        run = connection.execute(
            select(identity_sync_runs).where(
                identity_sync_runs.c.sync_run_id == result.sync_run_id
            )
        ).mappings().one()
    assert run["status"] == "SUCCESS"
    assert run["details"] == {"source": "test-adapter"}


def test_complete_snapshot_softly_deactivates_missing_identity(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "deactivate.sqlite3"
    service = IdentityDirectoryService(database_path)
    service.register_provider(_provider())
    service.synchronize("oracle-cloud-main", _snapshot())

    result = service.synchronize(
        "oracle-cloud-main",
        _snapshot(include_second_user=False),
    )

    assert result.identities_deactivated == 1
    assert len(service.list_external_identities("oracle-cloud-main")) == 1
    all_identities = service.list_external_identities(
        "oracle-cloud-main",
        include_inactive=True,
    )
    assert len(all_identities) == 2
    assert sum(not identity.active for identity in all_identities) == 1


def test_partial_snapshot_does_not_deactivate_unobserved_identity(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "partial.sqlite3"
    service = IdentityDirectoryService(database_path)
    service.register_provider(_provider())
    service.synchronize("oracle-cloud-main", _snapshot())
    partial = _snapshot(include_second_user=False)

    result = service.synchronize(
        "oracle-cloud-main",
        IdentityDirectorySnapshot(
            identities=partial.identities,
            retrieved_at=partial.retrieved_at + timedelta(seconds=1),
            complete=False,
        ),
    )

    assert result.status == IdentitySyncStatus.PARTIAL
    assert result.identities_deactivated == 0
    assert len(service.list_external_identities("oracle-cloud-main")) == 2


def test_safe_provider_configuration_rejects_secret_material(
    tmp_path: Path,
) -> None:
    service = IdentityDirectoryService(tmp_path / "secret.sqlite3")
    definition = IdentityProviderDefinition(
        code="unsafe-provider",
        provider_type=IdentityProviderType.OIDC,
        display_name="Unsafe",
        safe_configuration={"client_secret": "must-not-be-stored"},
    )

    with pytest.raises(IdentitySynchronizationError, match="secret material"):
        service.register_provider(definition)


def test_federated_only_user_cannot_authenticate_with_local_password(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "federated-user.sqlite3"
    database = database_for(database_path)
    now = datetime.now(UTC)
    with database.begin() as connection:
        connection.execute(
            insert(platform_users).values(
                username="federated@example.com",
                display_name="Federated User",
                email="federated@example.com",
                password_hash=None,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )

    access_control = AccessControlService(database_path)

    assert access_control.authenticate(
        "federated@example.com",
        "a password that must not work",
    ) is None
    with database.connect() as connection:
        row = connection.execute(select(external_identities)).first()
    assert row is None


def test_preview_is_read_only_and_reports_add_update_and_deactivation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "preview.sqlite3"
    service = IdentityDirectoryService(database_path)
    provider = _provider()
    service.register_provider(provider)
    first = _snapshot()
    service.synchronize(provider.code, first)
    changed_user = ExternalIdentitySnapshot(
        subject="ocid-user-1",
        username="planner@example.com",
        display_name="Senior Planning User",
        email="planner@example.com",
        entitlements=first.identities[0].entitlements,
    )
    added_user = ExternalIdentitySnapshot(
        subject="ocid-user-3",
        username="new@example.com",
        display_name="New Planner",
    )
    snapshot = IdentityDirectorySnapshot(
        identities=(changed_user, added_user),
        retrieved_at=datetime.now(UTC),
    )

    preview = service.preview_snapshot(provider, snapshot)

    assert preview.additions == 1
    assert preview.updates == 1
    assert preview.deactivations == 1
    assert {entry.action for entry in preview.entries} == {
        IdentityPreviewAction.ADD,
        IdentityPreviewAction.UPDATE,
        IdentityPreviewAction.DEACTIVATE,
    }
    assert len(service.list_external_identities(provider.code)) == 2


def test_snapshot_checksum_excludes_retrieval_time(tmp_path: Path) -> None:
    service = IdentityDirectoryService(tmp_path / "checksum.sqlite3")
    first = _snapshot()
    later = IdentityDirectorySnapshot(
        identities=first.identities,
        retrieved_at=first.retrieved_at + timedelta(minutes=5),
        complete=first.complete,
        details={"request_id": "different"},
    )

    assert service.snapshot_checksum(first) == service.snapshot_checksum(later)
