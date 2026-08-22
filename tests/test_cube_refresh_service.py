"""Tests for saved Planning Cube Refresh job execution."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.clients.epm_client import EPMClient
from app.services.cube_refresh_service import CubeRefreshService
from app.utils.exceptions import APIRequestError, CubeRefreshError


def _client() -> Mock:
    client = Mock(spec=EPMClient)
    client.application_name = "Vision"
    client.planning_api_root = "HyperionPlanning/rest/v3"
    return client


def test_cube_refresh_uses_documented_job_payload() -> None:
    client = _client()
    client.post.return_value = {"jobId": 701, "status": -1}

    submission = CubeRefreshService(client).start_refresh("Refresh Vision")

    assert submission.job_id == 701
    client.post.assert_called_once_with(
        "HyperionPlanning/rest/v3/applications/Vision/jobs",
        payload={
            "jobType": "CUBE_REFRESH",
            "jobName": "Refresh Vision",
        },
    )


def test_cube_refresh_rejects_immediate_oracle_failure() -> None:
    client = _client()
    client.post.return_value = {
        "jobId": 702,
        "status": 1,
        "details": "Refresh job is invalid",
    }

    with pytest.raises(CubeRefreshError, match="invalid"):
        CubeRefreshService(client).start_refresh("Refresh Vision")


def test_cube_refresh_explains_cloud_job_lookup_failure() -> None:
    client = _client()
    client.post.side_effect = APIRequestError(
        "Unable to find a job with the specified name.",
        status_code=400,
    )

    with pytest.raises(
        CubeRefreshError,
        match="Confirm that the job still exists",
    ):
        CubeRefreshService(client).start_refresh("RefreshCube")
