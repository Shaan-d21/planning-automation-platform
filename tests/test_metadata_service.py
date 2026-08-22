"""Tests for Oracle Planning metadata job submission."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.clients.epm_client import EPMClient
from app.models.metadata_job import MetadataImportMode
from app.services.metadata_service import MetadataService
from app.utils.exceptions import MetadataImportError


@pytest.fixture
def client() -> Mock:
    client = Mock(spec=EPMClient)
    client.application_name = "Vision"
    client.planning_api_root = "HyperionPlanning/rest/v3"
    return client


def test_start_import_submits_zip_override(client: Mock) -> None:
    client.post.return_value = {
        "jobId": 125,
        "status": -1,
        "descriptiveStatus": "Processing",
    }

    submission = MetadataService(client).start_import(
        "metadata.zip",
        "Import Metadata",
        error_file_name="metadata_errors.zip",
    )

    assert submission.job_id == 125
    assert submission.import_mode is MetadataImportMode.JOB_DEFINITION
    client.post.assert_called_once_with(
        "HyperionPlanning/rest/v3/applications/Vision/jobs",
        payload={
            "jobType": "IMPORT_METADATA",
            "jobName": "Import Metadata",
            "parameters": {
                "importZipFileName": "metadata.zip",
                "errorFile": "metadata_errors.zip",
            },
        },
    )


def test_start_import_uses_saved_job_filename_for_csv(client: Mock) -> None:
    client.post.return_value = {"jobId": "126", "status": -1}

    submission = MetadataService(client).start_import(
        "Account.csv",
        "Import Account",
    )

    assert submission.job_id == 126
    payload = client.post.call_args.kwargs["payload"]
    assert payload["parameters"] == {}


def test_start_import_uses_files_configured_in_saved_job(client: Mock) -> None:
    client.post.return_value = {"jobId": "128", "status": -1}

    submission = MetadataService(client).start_import(
        None,
        "Import Configured Metadata",
    )

    assert submission.file_name is None
    payload = client.post.call_args.kwargs["payload"]
    assert payload["parameters"] == {}


def test_start_import_rejects_unsupported_mode(client: Mock) -> None:
    with pytest.raises(
        MetadataImportError,
        match="configure merge/clear behavior",
    ):
        MetadataService(client).start_import(
            "Account.csv",
            "Import Account",
            import_mode="replace",
        )

    client.post.assert_not_called()


def test_start_import_rejects_immediate_job_error(client: Mock) -> None:
    client.post.return_value = {
        "jobId": 127,
        "status": 4,
        "details": "Invalid parameter",
    }

    with pytest.raises(MetadataImportError, match="Invalid parameter"):
        MetadataService(client).start_import(
            "metadata.zip",
            "Import Metadata",
        )
