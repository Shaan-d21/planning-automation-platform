"""Preview-first identity synchronization application tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.application.identity_access import IdentityAccessApplicationService
from app.config.settings import Settings
from app.services.access_control_service import AccessControlService
from app.utils.exceptions import IdentitySnapshotChangedError


class _Client:
    def __init__(self, *, role_name: str = "User") -> None:
        self.role_name = role_name
        self.closed = False

    def get(self, endpoint: str, *, params=None):
        if endpoint.endswith("roleassignmentreport/user"):
            return {
                "status": 0,
                "details": [
                    {
                        "userlogin": "planner@example.com",
                        "firstname": "Finance",
                        "lastname": "Planner",
                        "email": "planner@example.com",
                        "roles": [
                            {
                                "rolename": self.role_name,
                                "roletype": "Application",
                                "grantedthroughgroup": "",
                            }
                        ],
                    }
                ],
            }
        return {"status": 0, "details": []}

    def close(self) -> None:
        self.closed = True


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        epm_base_url="https://example.epm.oraclecloud.com",
        epm_username="administrator",
        epm_password="secret",
        application_name="Planning",
        deployment_mode="cloud",
        workflow_database_file=tmp_path / "identity.sqlite3",
    )


def test_preview_then_apply_persists_external_identity_only(tmp_path: Path) -> None:
    clients: list[_Client] = []

    def factory(_settings: Settings) -> _Client:
        client = _Client()
        clients.append(client)
        return client

    settings = _settings(tmp_path)
    administrator = AccessControlService(
        settings.database_target
    ).bootstrap_administrator(
        username="administrator",
        display_name="Administrator",
        email="administrator@example.com",
        password="A secure test password!",
    )
    service = IdentityAccessApplicationService(
        settings,
        client_factory=factory,
    )

    preview = service.preview()
    result, applied = service.synchronize(
        preview.snapshot_checksum,
        initiated_by_user_id=administrator.user_id,
    )

    assert preview.additions == 1
    assert applied.snapshot_checksum == preview.snapshot_checksum
    assert result.identities_seen == 1
    assert service.status()["synced_identities"] == 1
    assert all(client.closed for client in clients)


def test_apply_rejects_oracle_drift_after_preview(tmp_path: Path) -> None:
    calls = 0

    def factory(_settings: Settings) -> _Client:
        nonlocal calls
        calls += 1
        return _Client(role_name="User" if calls == 1 else "Power User")

    service = IdentityAccessApplicationService(
        _settings(tmp_path),
        client_factory=factory,
    )
    preview = service.preview()

    with pytest.raises(IdentitySnapshotChangedError, match="changed"):
        service.synchronize(
            preview.snapshot_checksum,
            initiated_by_user_id=1,
        )

    assert service.status()["synced_identities"] == 0
