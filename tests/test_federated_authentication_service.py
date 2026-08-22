"""Oracle OIDC subject binding and federated session security tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config.settings import Settings
from app.identity.oracle_epm_provider import OracleEPMIdentityProvider
from app.identity.oracle_oidc import OracleOIDCClaims
from app.infrastructure.database.engine import database_for
from app.infrastructure.database.schema import (
    authentication_events,
    external_identities,
)
from app.models.access_control import RoleCode
from app.models.identity import (
    ExternalEntitlementSnapshot,
    ExternalEntitlementType,
    ExternalIdentitySnapshot,
    IdentityDirectorySnapshot,
    IdentityProviderDefinition,
    IdentityProviderType,
)
from app.services.access_control_service import AccessControlService
from app.services.federated_authentication_service import (
    FederatedAuthenticationService,
)
from app.services.federated_provisioning_service import FederatedProvisioningService
from app.services.identity_directory_service import IdentityDirectoryService
from app.utils.exceptions import FederatedAuthenticationError
from app.web.application import create_app


def _settings(tmp_path: Path, *, federated: bool = False) -> Settings:
    return Settings(
        epm_base_url="https://example.epm.oraclecloud.com",
        epm_username="administrator",
        epm_password="secret",
        application_name="Planning",
        deployment_mode="cloud",
        workflow_database_file=tmp_path / "oidc.sqlite3",
        identity_provider="oracle_cloud" if federated else "local",
        oracle_identity_issuer_url=(
            "https://idcs.example.com" if federated else None
        ),
        oracle_identity_client_id=("client-id" if federated else None),
    )


def _provision(
    database_path: Path,
    provider_code: str,
) -> tuple[AccessControlService, int]:
    access = AccessControlService(database_path)
    administrator = access.bootstrap_administrator(
        username="administrator",
        display_name="Administrator",
        email="administrator@platform.test",
        password="a secure administrator password",
    )
    directory = IdentityDirectoryService(database_path)
    directory.register_provider(
        IdentityProviderDefinition(
            code=provider_code,
            provider_type=IdentityProviderType.ORACLE_CLOUD,
            display_name="Oracle Cloud Identity",
        )
    )
    entitlement = ExternalEntitlementSnapshot(
        external_key="Planning User",
        display_name="Planning User",
        entitlement_type=ExternalEntitlementType.APPLICATION_ROLE,
    )
    directory.synchronize(
        provider_code,
        IdentityDirectorySnapshot(
            identities=(
                ExternalIdentitySnapshot(
                    subject="epm-login:planner@example.com",
                    username="planner@example.com",
                    display_name="Finance Planner",
                    email="planner@example.com",
                    entitlements=(entitlement,),
                ),
            ),
            retrieved_at=datetime.now(UTC),
        ),
        initiated_by_user_id=administrator.user_id,
    )
    entitlement_id = directory.list_entitlements(provider_code)[0].entitlement_id
    directory.set_role_mapping(
        provider_code,
        entitlement_id,
        RoleCode.USER,
        actor_user_id=administrator.user_id,
    )
    provisioning = FederatedProvisioningService(database_path)
    preview = provisioning.preview(provider_code)
    provisioning.apply(
        provider_code,
        preview.checksum,
        actor_user_id=administrator.user_id,
    )
    return access, administrator.user_id


def test_first_login_binds_immutable_subject_and_audits_success(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "binding.sqlite3"
    access, _ = _provision(database_path, "oracle-main")
    service = FederatedAuthenticationService(database_path)

    user = service.authenticate(
        "oracle-main",
        subject="immutable-oracle-subject",
        username="PLANNER@example.com",
        ip_address="127.0.0.1",
    )

    assert user.username == "planner@example.com"
    assert user.roles == (RoleCode.USER,)
    assert access.authenticate("planner@example.com", "any local password") is None
    database = database_for(database_path)
    with database.connect() as connection:
        identity = connection.execute(select(external_identities)).mappings().one()
        success = connection.execute(
            select(authentication_events).where(
                authentication_events.c.event_type == "FEDERATED_LOGIN_SUCCESS"
            )
        ).mappings().one()
    assert identity["authenticated_subject"] == "immutable-oracle-subject"
    assert identity["last_authenticated_at"] is not None
    assert success["details"]["subject_bound"] is True


def test_bound_username_cannot_be_rebound_to_another_subject(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "rebind.sqlite3"
    _provision(database_path, "oracle-main")
    service = FederatedAuthenticationService(database_path)
    service.authenticate(
        "oracle-main",
        subject="subject-one",
        username="planner@example.com",
    )

    with pytest.raises(FederatedAuthenticationError, match="approved"):
        service.authenticate(
            "oracle-main",
            subject="subject-two",
            username="planner@example.com",
        )

    database = database_for(database_path)
    with database.connect() as connection:
        identity = connection.execute(select(external_identities)).mappings().one()
        failure = connection.execute(
            select(authentication_events)
            .where(authentication_events.c.event_type == "FEDERATED_LOGIN_FAILED")
            .order_by(authentication_events.c.event_id.desc())
        ).mappings().first()
    assert identity["authenticated_subject"] == "subject-one"
    assert failure is not None
    assert failure["details"]["reason"] == "IDENTITY_NOT_FOUND"


def test_federated_login_rejects_unprovisioned_synchronized_identity(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "unprovisioned.sqlite3"
    access = AccessControlService(database_path)
    administrator = access.bootstrap_administrator(
        username="administrator",
        display_name="Administrator",
        email=None,
        password="a secure administrator password",
    )
    directory = IdentityDirectoryService(database_path)
    directory.register_provider(
        IdentityProviderDefinition(
            code="oracle-main",
            provider_type=IdentityProviderType.ORACLE_CLOUD,
            display_name="Oracle",
        )
    )
    directory.synchronize(
        "oracle-main",
        IdentityDirectorySnapshot(
            identities=(ExternalIdentitySnapshot(
                subject="epm-login:planner@example.com",
                username="planner@example.com",
                display_name="Planner",
            ),),
            retrieved_at=datetime.now(UTC),
        ),
        initiated_by_user_id=administrator.user_id,
    )

    with pytest.raises(FederatedAuthenticationError, match="provision"):
        FederatedAuthenticationService(database_path).authenticate(
            "oracle-main",
            subject="subject-one",
            username="planner@example.com",
        )


class _FakeOIDC:
    def __init__(self) -> None:
        self.redirect_uri: str | None = None

    async def begin(self, request, redirect_uri: str):
        self.redirect_uri = redirect_uri
        return RedirectResponse(
            "https://idcs.example.com/oauth2/v1/authorize",
            status_code=302,
        )

    async def complete(self, request) -> OracleOIDCClaims:
        return OracleOIDCClaims(
            subject="browser-subject",
            username="planner@example.com",
            display_name="Finance Planner",
            email="planner@example.com",
        )


def test_oidc_callback_starts_platform_session_for_approved_shadow_user(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, federated=True)
    provider_code = OracleEPMIdentityProvider.definition_for(settings).code
    _provision(settings.workflow_database_file, provider_code)  # type: ignore[arg-type]
    app = create_app(settings, session_secret="test-session-secret")
    fake = _FakeOIDC()
    app.state.oracle_oidc = fake
    client = TestClient(app)

    advertised = client.get("/api/v1/bootstrap").json()
    started = client.get("/auth/oracle/start", follow_redirects=False)
    callback = client.get(
        "/auth/oracle/callback?code=one-time-code&state=validated-state",
        follow_redirects=False,
    )
    authenticated = client.get("/api/v1/bootstrap").json()

    assert advertised["identity_authentication"]["federated_enabled"] is True
    assert advertised["identity_authentication"]["login_url"] == "/auth/oracle/start"
    assert started.status_code == 302
    assert fake.redirect_uri == "http://testserver/auth/oracle/callback"
    assert callback.status_code == 303
    assert callback.headers["location"] == "/"
    assert authenticated["authenticated"] is True
    assert authenticated["user"]["username"] == "planner@example.com"
