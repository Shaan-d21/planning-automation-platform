"""Security-focused tests for platform identities and authorization."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.access_control import Permission, RoleCode
from app.services.access_control_service import (
    AccessControlService,
    PasswordHasher,
)
from app.utils.exceptions import AccessControlError


def test_first_administrator_is_atomic_and_has_all_permissions(
    tmp_path: Path,
) -> None:
    service = AccessControlService(tmp_path / "history.sqlite3")

    administrator = service.bootstrap_administrator(
        username="admin",
        display_name="Platform Owner",
        email="owner@example.com",
        password="Secure passphrase 123!",
    )

    assert service.requires_bootstrap is False
    assert administrator.roles == (RoleCode.SERVICE_ADMINISTRATOR,)
    assert administrator.permissions == frozenset(Permission)
    assert service.authenticate("ADMIN", "Secure passphrase 123!") is not None
    assert service.authenticate("admin", "wrong password") is None
    with pytest.raises(AccessControlError, match="already"):
        service.bootstrap_administrator(
            username="second",
            display_name="Second Owner",
            email=None,
            password="Another passphrase 123!",
        )


def test_user_roles_enforce_capabilities_and_last_admin_is_protected(
    tmp_path: Path,
) -> None:
    service = AccessControlService(tmp_path / "history.sqlite3")
    admin = service.bootstrap_administrator(
        username="admin",
        display_name="Platform Owner",
        email=None,
        password="Secure passphrase 123!",
    )
    operator = service.create_user(
        username="planner",
        display_name="Forecast Planner",
        email="planner@example.com",
        password="Planner passphrase 123!",
        roles=(RoleCode.POWER_USER,),
        actor_user_id=admin.user_id,
    )

    assert operator.has_permission(Permission.PROCESS_RUN)
    assert operator.has_permission(Permission.DATA_REVIEW)
    assert not operator.has_permission(Permission.PROCESS_DESIGN)
    with pytest.raises(AccessControlError, match="last active"):
        service.update_user(
            admin.user_id,
            display_name=admin.display_name,
            email=admin.email,
            active=False,
            roles=admin.roles,
            actor_user_id=admin.user_id,
        )


def test_business_roles_keep_planner_and_viewer_access_least_privileged(
    tmp_path: Path,
) -> None:
    service = AccessControlService(tmp_path / "history.sqlite3")
    admin = service.bootstrap_administrator(
        username="admin",
        display_name="Platform Owner",
        email=None,
        password="Secure passphrase 123!",
    )
    planner = service.create_user(
        username="planner",
        display_name="Forecast Planner",
        email=None,
        password="Planner passphrase 123!",
        roles=(RoleCode.USER,),
        actor_user_id=admin.user_id,
    )
    viewer = service.create_user(
        username="viewer",
        display_name="Executive Viewer",
        email=None,
        password="Viewer passphrase 123!",
        roles=(RoleCode.VIEWER,),
        actor_user_id=admin.user_id,
    )

    assert planner.has_permission(Permission.PROCESS_RUN)
    assert planner.has_permission(Permission.DATA_REVIEW)
    assert not planner.has_permission(Permission.OPERATION_EXECUTE)
    assert viewer.has_permission(Permission.REPORT_GENERATE)
    assert not viewer.has_permission(Permission.DATA_REVIEW)
    assert not viewer.has_permission(Permission.OPERATION_EXECUTE)


def test_every_user_has_exactly_one_primary_role(tmp_path: Path) -> None:
    service = AccessControlService(tmp_path / "history.sqlite3")
    admin = service.bootstrap_administrator(
        username="admin",
        display_name="Platform Owner",
        email=None,
        password="Secure passphrase 123!",
    )

    with pytest.raises(AccessControlError, match="exactly one"):
        service.create_user(
            username="mixed",
            display_name="Mixed Role User",
            email=None,
            password="Mixed role password 123!",
            roles=(RoleCode.POWER_USER, RoleCode.USER),
            actor_user_id=admin.user_id,
        )


def test_password_hash_is_salted_and_never_contains_plaintext() -> None:
    first = PasswordHasher.hash("Secure passphrase 123!")
    second = PasswordHasher.hash("Secure passphrase 123!")

    assert first != second
    assert "Secure passphrase" not in first
    assert PasswordHasher.verify("Secure passphrase 123!", first)
    assert not PasswordHasher.verify("Incorrect passphrase", first)
