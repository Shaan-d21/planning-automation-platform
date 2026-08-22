"""Tests for Oracle Planning Data Map REST execution."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.clients.epm_client import EPMClient
from app.services.data_map_service import DataMapService
from app.utils.exceptions import DataMapError


@pytest.fixture
def client() -> Mock:
    client = Mock(spec=EPMClient)
    client.application_name = "Vision"
    client.planning_api_root = "HyperionPlanning/rest/v3"
    return client


def test_start_data_map_sends_clear_and_member_overrides(
    client: Mock,
) -> None:
    client.post.return_value = {
        "jobId": 901,
        "status": -1,
        "descriptiveStatus": "Processing",
    }

    submission = DataMapService(client).start_data_map(
        "Product_Revenue_to_Reporting",
        clear_target=False,
        member_overrides={"Period": "Jan,FEB"},
        exclusion_overrides={"Entity": "No Entity"},
    )

    assert submission.job_id == 901
    assert submission.request.clear_target is False
    client.post.assert_called_once_with(
        "HyperionPlanning/rest/v3/applications/Vision/jobs",
        payload={
            "jobType": "PLAN_TYPE_MAP",
            "jobName": "Product_Revenue_to_Reporting",
            "parameters": {
                "clearData": False,
                "overrideMembersMap": {"Period": "Jan,FEB"},
                "overrideExclusionMembersMap": {"Entity": "No Entity"},
            },
        },
    )


def test_start_data_map_rejects_immediate_oracle_failure(
    client: Mock,
) -> None:
    client.post.return_value = {
        "jobId": 902,
        "status": 1,
        "details": "Invalid member selection",
    }

    with pytest.raises(DataMapError, match="Invalid member selection"):
        DataMapService(client).start_data_map("Revenue Map")


def test_data_map_name_is_required(client: Mock) -> None:
    with pytest.raises(DataMapError, match="cannot be empty"):
        DataMapService(client).start_data_map(" ")
