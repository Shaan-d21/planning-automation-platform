"""Tests for the reusable Oracle EPM HTTP client."""

from __future__ import annotations

import socket
from io import BytesIO
from unittest.mock import Mock

import pytest
import requests
from requests.auth import HTTPBasicAuth

from app.clients.epm_client import EPMClient
from app.config.settings import Settings
from app.utils.exceptions import (
    APIRequestError,
    AuthenticationError,
    EPMConnectionError,
)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="epm.user",
        epm_password="secret",
        application_name="Plan 1",
        request_timeout=10,
    )


def json_response(status_code: int, payload: object) -> Mock:
    response = Mock(spec=requests.Response)
    response.status_code = status_code
    response.ok = 200 <= status_code < 400
    response.headers = {"Content-Type": "application/json"}
    response.content = b"content"
    response.json.return_value = payload
    response.text = str(payload)
    return response


def test_authenticate_uses_basic_auth_and_reuses_session(
    settings: Settings,
) -> None:
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.request.return_value = json_response(
        200,
        {"version": "v3", "lifecycle": "active", "isLatest": True},
    )

    client = EPMClient(settings, session=session)
    version_info = client.authenticate()

    assert version_info["version"] == "v3"
    assert client.is_authenticated is True
    assert isinstance(session.auth, HTTPBasicAuth)
    assert session.auth.username == "epm.user"
    assert session.auth.password == "secret"
    session.request.assert_called_once_with(
        method="GET",
        url="https://example.oraclecloud.com/HyperionPlanning/rest/v3",
        timeout=10,
        verify=True,
        params=None,
    )


def test_authenticate_accepts_hyperion_planning_base_url(
    settings: Settings,
) -> None:
    settings_with_path = Settings(
        epm_base_url=(
            "https://example.oraclecloud.com/HyperionPlanning"
        ),
        epm_username=settings.epm_username,
        epm_password=settings.epm_password,
        application_name=settings.application_name,
    )
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.request.return_value = json_response(200, {"version": "v3"})

    EPMClient(settings_with_path, session=session).authenticate()

    assert session.request.call_args.kwargs["url"] == (
        "https://example.oraclecloud.com/HyperionPlanning/rest/v3"
    )


def test_client_infers_cloud_deployment_from_hostname(
    settings: Settings,
) -> None:
    session = Mock(spec=requests.Session)
    session.headers = {}

    client = EPMClient(settings, session=session)

    assert client.deployment_mode == "cloud"
    assert client.is_cloud_environment is True


@pytest.mark.parametrize("status_code", [401, 403])
def test_authenticate_translates_authorization_errors(
    settings: Settings,
    status_code: int,
) -> None:
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.request.return_value = json_response(
        status_code,
        {"message": "Not authorized"},
    )
    client = EPMClient(settings, session=session)

    with pytest.raises(AuthenticationError) as error:
        client.authenticate()

    assert error.value.status_code == status_code
    assert client.is_authenticated is False


def test_request_translates_connection_failure(settings: Settings) -> None:
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.request.side_effect = requests.exceptions.ConnectionError()

    with pytest.raises(EPMConnectionError, match="Unable to connect"):
        EPMClient(settings, session=session).authenticate()


def test_request_reports_dns_resolution_failure(settings: Settings) -> None:
    session = Mock(spec=requests.Session)
    session.headers = {}
    dns_error = socket.gaierror(11001, "getaddrinfo failed")
    connection_error = requests.exceptions.ConnectionError(dns_error)
    session.request.side_effect = connection_error

    with pytest.raises(
        EPMConnectionError,
        match=(
            "DNS resolution failed for Oracle EPM host "
            "'example.oraclecloud.com'"
        ),
    ):
        EPMClient(settings, session=session).authenticate()


def test_request_translates_api_error(settings: Settings) -> None:
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.request.return_value = json_response(
        404,
        {"message": "Application was not found"},
    )

    with pytest.raises(APIRequestError, match="Application was not found"):
        EPMClient(settings, session=session).authenticate()


def test_client_rejects_absolute_endpoint(settings: Settings) -> None:
    session = Mock(spec=requests.Session)
    session.headers = {}
    client = EPMClient(settings, session=session)

    with pytest.raises(ValueError, match="relative path"):
        client.get("https://untrusted.example.com/path")

    session.request.assert_not_called()


def test_post_binary_uses_octet_stream_and_environment_root(
    settings: Settings,
) -> None:
    settings_with_path = Settings(
        epm_base_url=(
            "https://example.oraclecloud.com/HyperionPlanning"
        ),
        epm_username=settings.epm_username,
        epm_password=settings.epm_password,
        application_name=settings.application_name,
    )
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.request.return_value = json_response(200, {"status": 0})
    content = BytesIO(b"metadata")

    result = EPMClient(
        settings_with_path,
        session=session,
    ).post_binary(
        "interop/rest/test/upload",
        content,
    )

    assert result == {"status": 0}
    session.request.assert_called_once_with(
        method="POST",
        url="https://example.oraclecloud.com/interop/rest/test/upload",
        timeout=30,
        verify=True,
        params=None,
        data=content,
        headers={"Content-Type": "application/octet-stream"},
    )


def test_get_binary_preserves_repository_content(settings: Settings) -> None:
    session = Mock(spec=requests.Session)
    session.headers = {}
    response = json_response(200, {})
    response.headers = {"Content-Type": "application/octet-stream"}
    response.content = b"PK\x03\x04calc-manager"
    session.request.return_value = response

    content = EPMClient(settings, session=session).get_binary(
        "interop/rest/test/export.zip/contents"
    )

    assert content == b"PK\x03\x04calc-manager"
    session.request.assert_called_once_with(
        method="GET",
        url=(
            "https://example.oraclecloud.com/interop/rest/test/"
            "export.zip/contents"
        ),
        timeout=10,
        verify=True,
        params=None,
    )
def test_data_integration_endpoint_uses_environment_root(
    settings: Settings,
) -> None:
    settings_with_path = Settings(
        epm_base_url=(
            "https://example.oraclecloud.com/HyperionPlanning"
        ),
        epm_username=settings.epm_username,
        epm_password=settings.epm_password,
        application_name=settings.application_name,
    )
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.request.return_value = json_response(
        200,
        {"jobId": 5, "status": -1},
    )

    EPMClient(settings_with_path, session=session).post(
        "aif/rest/V1/jobs",
        payload={"jobType": "INTEGRATION"},
    )

    assert session.request.call_args.kwargs["url"] == (
        "https://example.oraclecloud.com/aif/rest/V1/jobs"
    )


def test_delete_uses_shared_session(settings: Settings) -> None:
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.request.return_value = json_response(200, {"status": 0})

    result = EPMClient(settings, session=session).delete(
        "interop/rest/test/file"
    )

    assert result == {"status": 0}
    session.request.assert_called_once_with(
        method="DELETE",
        url="https://example.oraclecloud.com/interop/rest/test/file",
        timeout=10,
        verify=True,
        params=None,
    )
