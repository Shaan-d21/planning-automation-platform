"""Tests for native Oracle Planning data job submission."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.clients.epm_client import EPMClient
from app.services.data_service import DataService
from app.utils.exceptions import DataImportError


@pytest.fixture
def client() -> Mock:
    client = Mock(spec=EPMClient)
    client.application_name = "Vision"
    client.planning_api_root = "HyperionPlanning/rest/v3"
    return client


@pytest.mark.parametrize(
    "file_name",
    ["PlanData.csv", "PlanData.txt", "PlanData.zip"],
)
def test_start_import_sends_documented_file_override(
    client: Mock,
    file_name: str,
) -> None:
    client.post.return_value = {
        "jobId": 201,
        "status": -1,
        "descriptiveStatus": "Processing",
    }

    submission = DataService(client).start_import(
        file_name,
        "Import Plan Data",
        error_file_name="DataErrors.zip",
    )

    assert submission.job_id == 201
    assert submission.file_name == file_name
    client.post.assert_called_once_with(
        "HyperionPlanning/rest/v3/applications/Vision/jobs",
        payload={
            "jobType": "IMPORT_DATA",
            "jobName": "Import Plan Data",
            "parameters": {
                "importFileName": file_name,
                "errorFile": "DataErrors.zip",
            },
        },
    )


def test_start_import_rejects_unsupported_extension(client: Mock) -> None:
    with pytest.raises(DataImportError, match=r"\.csv, \.txt, or \.zip"):
        DataService(client).start_import(
            "PlanData.xlsx",
            "Import Plan Data",
        )

    client.post.assert_not_called()


def test_start_import_can_use_file_saved_in_oracle_job(client: Mock) -> None:
    client.post.return_value = {
        "jobId": 203,
        "status": -1,
        "descriptiveStatus": "Processing",
    }

    submission = DataService(client).start_import(
        None,
        "Import Plan Data",
        error_file_name="DataErrors-run12345.zip",
    )

    assert submission.file_name is None
    client.post.assert_called_once_with(
        "HyperionPlanning/rest/v3/applications/Vision/jobs",
        payload={
            "jobType": "IMPORT_DATA",
            "jobName": "Import Plan Data",
            "parameters": {"errorFile": "DataErrors-run12345.zip"},
        },
    )


def test_start_import_rejects_immediate_job_error(client: Mock) -> None:
    client.post.return_value = {
        "jobId": 202,
        "status": 4,
        "details": "Invalid import file",
    }

    with pytest.raises(DataImportError, match="Invalid import file"):
        DataService(client).start_import(
            "PlanData.csv",
            "Import Plan Data",
        )
