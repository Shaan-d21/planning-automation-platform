"""Collision-safe Oracle entitlement mapping and provisioning tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.infrastructure.database.engine import database_for
from app.infrastructure.database.schema import external_identities, platform_users
from app.models.access_control import RoleCode
from app.models.identity import (
    ExternalEntitlementSnapshot,
    ExternalEntitlementType,
    ExternalIdentitySnapshot,
    IdentityDirectorySnapshot,
    IdentityProviderDefinition,
    IdentityProviderType,
    IdentityProvisioningAction,
)
from app.services.access_control_service import AccessControlService
from app.services.federated_provisioning_service import FederatedProvisioningService
from app.services.identity_directory_service import IdentityDirectoryService
from app.utils.exceptions import IdentitySnapshotChangedError


PROVIDER_CODE = "oracle-cloud-main"


def _setup(database_path: Path, *, admin_username: str = "admin"):
    access = AccessControlService(database_path)
    administrator = access.bootstrap_administrator(
        username=admin_username,
        display_name="Platform Administrator",
        email="administrator@platform.test",
        password="a secure admin password",
    )
    directory = IdentityDirectoryService(database_path)
    directory.register_provider(
        IdentityProviderDefinition(
            code=PROVIDER_CODE,
            provider_type=IdentityProviderType.ORACLE_CLOUD,
            display_name="Oracle Cloud EPM",
        )
    )
    planner = ExternalEntitlementSnapshot(
        external_key="Planning User",
        display_name="Planning User",
        entitlement_type=ExternalEntitlementType.APPLICATION_ROLE,
    )
    finance = ExternalEntitlementSnapshot(
        external_key="Finance Planners",
        display_name="Finance Planners",
        entitlement_type=ExternalEntitlementType.GROUP,
    )
    directory.synchronize(
        PROVIDER_CODE,
        IdentityDirectorySnapshot(
            identities=(
                ExternalIdentitySnapshot(
                    subject="oracle-1",
                    username="planner@example.com",
                    display_name="Planning User",
                    email="planner@example.com",
                    entitlements=(planner, finance),
                ),
                ExternalIdentitySnapshot(
                    subject="oracle-2",
                    username="viewer@example.com",
                    display_name="Planning Viewer",
                    email="viewer@example.com",
                    entitlements=(planner,),
                ),
            ),
            retrieved_at=datetime.now(UTC),
        ),
        initiated_by_user_id=administrator.user_id,
    )
    return access, administrator, directory


def _entitlement_id(directory: IdentityDirectoryService, name: str) -> int:
    return next(
        item.entitlement_id
        for item in directory.list_entitlements(PROVIDER_CODE)
        if item.display_name == name
    )


def test_explicit_mappings_create_passwordless_shadow_users_with_highest_role(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "provision.sqlite3"
    access, administrator, directory = _setup(database_path)
    directory.set_role_mapping(
        PROVIDER_CODE,
        _entitlement_id(directory, "Planning User"),
        RoleCode.USER,
        actor_user_id=administrator.user_id,
    )
    directory.set_role_mapping(
        PROVIDER_CODE,
        _entitlement_id(directory, "Finance Planners"),
        RoleCode.POWER_USER,
        actor_user_id=administrator.user_id,
    )
    service = FederatedProvisioningService(database_path)

    preview = service.preview(PROVIDER_CODE)

    assert preview.creates == 2
    assert {
        entry.username: entry.target_role for entry in preview.entries
    } == {
        "planner@example.com": RoleCode.POWER_USER.value,
        "viewer@example.com": RoleCode.USER.value,
    }
    result, _ = service.apply(
        PROVIDER_CODE,
        preview.checksum,
        actor_user_id=administrator.user_id,
    )
    assert result.created == 2
    users = {user.username: user for user in access.list_users()}
    assert users["planner@example.com"].roles == (RoleCode.POWER_USER,)
    assert users["viewer@example.com"].roles == (RoleCode.USER,)
    database = database_for(database_path)
    with database.connect() as connection:
        shadows = connection.execute(
            select(platform_users).where(
                platform_users.c.username.in_(
                    ("planner@example.com", "viewer@example.com")
                )
            )
        ).mappings().all()
        links = connection.execute(
            select(external_identities.c.user_id).where(
                external_identities.c.provider_id.is_not(None)
            )
        ).scalars().all()
    assert all(row["password_hash"] is None for row in shadows)
    assert all(user_id is not None for user_id in links)
    assert service.preview(PROVIDER_CODE).unchanged == 2


def test_local_username_collision_is_protected_and_never_linked(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "collision.sqlite3"
    _, administrator, directory = _setup(
        database_path,
        admin_username="planner@example.com",
    )
    directory.set_role_mapping(
        PROVIDER_CODE,
        _entitlement_id(directory, "Planning User"),
        RoleCode.USER,
        actor_user_id=administrator.user_id,
    )
    service = FederatedProvisioningService(database_path)

    preview = service.preview(PROVIDER_CODE)
    conflict = next(
        entry for entry in preview.entries if entry.username == "planner@example.com"
    )

    assert conflict.action == IdentityProvisioningAction.CONFLICT
    result, _ = service.apply(
        PROVIDER_CODE,
        preview.checksum,
        actor_user_id=administrator.user_id,
    )
    assert result.conflicts == 1
    database = database_for(database_path)
    with database.connect() as connection:
        local = connection.execute(
            select(platform_users).where(
                platform_users.c.username == "planner@example.com"
            )
        ).mappings().one()
        external = connection.execute(
            select(external_identities).where(
                external_identities.c.username == "planner@example.com"
            )
        ).mappings().one()
    assert local["password_hash"]
    assert external["user_id"] is None


def test_removing_last_mapping_deactivates_managed_shadow_users(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "deactivate.sqlite3"
    access, administrator, directory = _setup(database_path)
    planning_role = _entitlement_id(directory, "Planning User")
    directory.set_role_mapping(
        PROVIDER_CODE,
        planning_role,
        RoleCode.USER,
        actor_user_id=administrator.user_id,
    )
    service = FederatedProvisioningService(database_path)
    first = service.preview(PROVIDER_CODE)
    service.apply(
        PROVIDER_CODE,
        first.checksum,
        actor_user_id=administrator.user_id,
    )
    directory.remove_role_mapping(PROVIDER_CODE, planning_role)

    preview = service.preview(PROVIDER_CODE)

    assert preview.deactivations == 2
    result, _ = service.apply(
        PROVIDER_CODE,
        preview.checksum,
        actor_user_id=administrator.user_id,
    )
    assert result.deactivated == 2
    shadow_users = [user for user in access.list_users() if user.user_id != administrator.user_id]
    assert shadow_users
    assert all(not user.active for user in shadow_users)


def test_mapping_change_invalidates_reviewed_provisioning_checksum(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "drift.sqlite3"
    _, administrator, directory = _setup(database_path)
    planning_role = _entitlement_id(directory, "Planning User")
    directory.set_role_mapping(
        PROVIDER_CODE,
        planning_role,
        RoleCode.USER,
        actor_user_id=administrator.user_id,
    )
    service = FederatedProvisioningService(database_path)
    preview = service.preview(PROVIDER_CODE)
    directory.set_role_mapping(
        PROVIDER_CODE,
        planning_role,
        RoleCode.POWER_USER,
        actor_user_id=administrator.user_id,
    )

    with pytest.raises(IdentitySnapshotChangedError, match="changed"):
        service.apply(
            PROVIDER_CODE,
            preview.checksum,
            actor_user_id=administrator.user_id,
        )
