"""Oracle Cloud EPM Access Control identity adapter tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.settings import Settings
from app.identity.oracle_epm_provider import OracleEPMIdentityProvider
from app.models.identity import (
    ExternalAssignmentType,
    ExternalEntitlementType,
)
from app.utils.exceptions import IdentitySynchronizationError


class _ReportClient:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, endpoint: str, *, params=None):
        self.calls.append(endpoint)
        return self.responses[endpoint]


def _settings(tmp_path: Path, *, deployment_mode: str = "cloud") -> Settings:
    return Settings(
        epm_base_url="https://example.epm.oraclecloud.com",
        epm_username="service.admin@example.com",
        epm_password="secret",
        application_name="Planning",
        deployment_mode=deployment_mode,
        workflow_database_file=tmp_path / "identity.sqlite3",
    )


def _responses() -> dict[str, object]:
    return {
        "interop/rest/security/v2/report/roleassignmentreport/user": {
            "status": 0,
            "error": None,
            "details": [
                {
                    "userlogin": "Planner@example.com",
                    "firstname": "Finance",
                    "lastname": "Planner",
                    "email": "planner@example.com",
                    "roles": [
                        {
                            "rolename": "Power User",
                            "roletype": "Application",
                            "grantedthroughgroup": "Finance Team",
                        },
                        {
                            "rolename": "Data Integration - Run",
                            "roletype": "Granular",
                            "grantedthroughgroup": "",
                        },
                    ],
                }
            ],
        },
        "interop/rest/security/v2/report/usergroupreport": {
            "status": 0,
            "error": None,
            "details": [
                {
                    "userlogin": "planner@example.com",
                    "firstname": "Finance",
                    "lastname": "Planner",
                    "email": "planner@example.com",
                    "groups": [
                        {"direct": "Yes", "groupname": "Finance Team"},
                        {"direct": "No", "groupname": "All Planners"},
                    ],
                }
            ],
        },
    }


def test_adapter_merges_roles_and_groups_by_normalized_user_login(
    tmp_path: Path,
) -> None:
    client = _ReportClient(_responses())
    provider = OracleEPMIdentityProvider(_settings(tmp_path), client)

    snapshot = provider.fetch_snapshot()

    assert snapshot.complete is True
    assert len(snapshot.identities) == 1
    identity = snapshot.identities[0]
    assert identity.subject == "epm-login:planner@example.com"
    assert identity.display_name == "Finance Planner"
    assert len(identity.entitlements) == 4
    application_role = next(
        item
        for item in identity.entitlements
        if item.entitlement_type == ExternalEntitlementType.APPLICATION_ROLE
    )
    assert application_role.assignment_type == ExternalAssignmentType.INHERITED
    assert application_role.granted_through_group == "Finance Team"
    groups = {
        item.external_key: item.assignment_type
        for item in identity.entitlements
        if item.entitlement_type == ExternalEntitlementType.GROUP
    }
    assert groups == {
        "All Planners": ExternalAssignmentType.INHERITED,
        "Finance Team": ExternalAssignmentType.DIRECT,
    }


def test_adapter_surfaces_oracle_report_failure(tmp_path: Path) -> None:
    responses = _responses()
    responses[
        "interop/rest/security/v2/report/roleassignmentreport/user"
    ] = {
        "status": 1,
        "error": {"errormessage": "Access Control authorization failed."},
        "details": None,
    }
    provider = OracleEPMIdentityProvider(
        _settings(tmp_path),
        _ReportClient(responses),
    )

    with pytest.raises(
        IdentitySynchronizationError,
        match="Access Control authorization failed",
    ):
        provider.fetch_snapshot()


def test_adapter_rejects_on_premises_environment(tmp_path: Path) -> None:
    provider = OracleEPMIdentityProvider(
        _settings(tmp_path, deployment_mode="on_premises"),
        _ReportClient(_responses()),
    )

    with pytest.raises(IdentitySynchronizationError, match="Oracle Cloud"):
        provider.fetch_snapshot()

