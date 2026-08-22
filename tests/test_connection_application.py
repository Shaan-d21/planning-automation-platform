"""Tests for application-level Oracle EPM connection verification."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.application.connection import VerifyConnection
from app.config.settings import Settings


def test_connection_verifies_the_configured_application() -> None:
    settings = Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="administrator",
        epm_password="secret",
        application_name="Vision",
    )
    client = MagicMock()
    client.__enter__.return_value = client
    client.application_name = "Vision"
    client.planning_api_root = "HyperionPlanning/rest/v3"
    client.deployment_mode = "cloud"
    client.get.return_value = {
        "name": "Vision",
        "appType": "PBCS",
        "appStorage": "Multidim",
        "hybrid": True,
    }

    result = VerifyConnection(
        settings,
        client_factory=lambda _: client,
    ).execute()

    client.authenticate.assert_called_once_with()
    assert result.application_name == "Vision"
    assert result.deployment_mode == "cloud"
    assert result.application_type == "PBCS"
    assert result.storage == "Multidim"
