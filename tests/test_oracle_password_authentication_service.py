"""Oracle credential login and just-in-time linked-profile tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config.settings import Settings
from app.identity.oracle_epm_provider import OracleEPMIdentityProvider
from app.infrastructure.database.engine import database_for
from app.infrastructure.database.schema import platform_users
from app.models.access_control import RoleCode
from app.models.identity import (
    ExternalEntitlementSnapshot,
    ExternalEntitlementType,
    IdentityDirectorySnapshot,
)
from app.services.access_control_service import AccessControlService
from app.services.identity_directory_service import IdentityDirectoryService
from app.services.oracle_password_authentication_service import (
    OraclePasswordAuthenticationService,
)
from app.utils.exceptions import (
    AuthenticationError,
    OracleCredentialAuthenticationError,
)


class _Client:
    def __init__(self, settings: Settings, *, reject: bool = False) -> None:
        self.settings = settings
        self.reject = reject
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def authenticate(self):
        if self.reject:
            raise AuthenticationError("Oracle rejected the credentials.")
        return {"version": "v3"}

    def post(self, endpoint: str, *, payload=None, params=None):
        assert endpoint.endswith("users/list")
        return {
            "status": 0,
            "details": [{
                "userlogin": "planner@example.com",
                "firstname": "Finance",
                "lastname": "Planner",
                "email": "planner@example.com",
                "applicationroles": [{
                    "rolename": "Power User",
                    "direct": "Yes",
                }],
                "granularroles": [],
                "epmgroups": [],
                "idcsgroups": [],
            }],
        }

    def get(self, endpoint: str, *, params=None):
        return {"status": 0, "details": []}

    def close(self) -> None:
        self.closed = True


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        epm_base_url="https://example.epm.oraclecloud.com",
        epm_username="integration.admin@example.com",
        epm_password="integration-secret",
        application_name="Planning",
        deployment_mode="cloud",
        workflow_database_file=tmp_path / "oracle-login.sqlite3",
    )


def _configure_mapping(settings: Settings) -> None:
    access = AccessControlService(settings.database_target)
    administrator = access.bootstrap_administrator(
        username="local-admin",
        display_name="Local Administrator",
        email="admin@example.com",
        password="A secure local password!",
    )
    directory = IdentityDirectoryService(settings.database_target)
    definition = OracleEPMIdentityProvider.definition_for(settings)
    directory.register_provider(definition)
    directory.synchronize(
        definition.code,
        IdentityDirectorySnapshot(
            identities=(),
            retrieved_at=datetime.now(UTC),
            available_entitlements=(ExternalEntitlementSnapshot(
                external_key="Power User",
                display_name="Power User",
                entitlement_type=ExternalEntitlementType.APPLICATION_ROLE,
            ),),
        ),
        initiated_by_user_id=administrator.user_id,
    )
    entitlement = directory.list_entitlements(definition.code)[0]
    directory.set_role_mapping(
        definition.code,
        entitlement.entitlement_id,
        RoleCode.POWER_USER,
        actor_user_id=administrator.user_id,
    )


def test_oracle_login_validates_credentials_and_creates_linked_profile(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _configure_mapping(settings)
    clients: list[_Client] = []

    def factory(client_settings: Settings):
        client = _Client(client_settings)
        clients.append(client)
        return client

    service = OraclePasswordAuthenticationService(
        settings,
        client_factory=factory,
    )

    user = service.authenticate(
        "planner@example.com",
        "temporary-oracle-password",
        ip_address="127.0.0.1",
    )

    assert user.username == "planner@example.com"
    assert user.roles == (RoleCode.POWER_USER,)
    assert user.last_login_at is not None
    assert clients[0].settings.epm_username == "planner@example.com"
    assert clients[1].settings.epm_username == "integration.admin@example.com"
    assert all(client.closed for client in clients)
    with database_for(settings.database_target).connect() as connection:
        row = connection.execute(
            select(platform_users).where(
                platform_users.c.user_id == user.user_id
            )
        ).mappings().one()
    assert row["password_hash"] is None


def test_invalid_oracle_credentials_do_not_create_a_profile(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _configure_mapping(settings)

    service = OraclePasswordAuthenticationService(
        settings,
        client_factory=lambda client_settings: _Client(
            client_settings,
            reject=client_settings.epm_username == "planner@example.com",
        ),
    )

    with pytest.raises(OracleCredentialAuthenticationError) as failure:
        service.authenticate("planner@example.com", "wrong-password")

    assert failure.value.credentials_valid is False
    users = AccessControlService(settings.database_target).list_users()
    assert {user.username for user in users} == {"local-admin"}


def test_repeated_failures_are_throttled_before_oracle_is_called_again(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _configure_mapping(settings)
    oracle_calls = 0

    def factory(client_settings: Settings):
        nonlocal oracle_calls
        if client_settings.epm_username == "planner@example.com":
            oracle_calls += 1
        return _Client(client_settings, reject=True)

    service = OraclePasswordAuthenticationService(
        settings,
        client_factory=factory,
    )
    for _ in range(3):
        with pytest.raises(OracleCredentialAuthenticationError):
            service.authenticate(
                "planner@example.com",
                "wrong-password",
                ip_address="127.0.0.1",
            )

    with pytest.raises(
        OracleCredentialAuthenticationError,
        match="Too many unsuccessful",
    ):
        service.authenticate(
            "planner@example.com",
            "wrong-password",
            ip_address="127.0.0.1",
        )

    assert oracle_calls == 3
