"""Tests for normalized Oracle Planning job retrieval."""

from __future__ import annotations

from unittest.mock import Mock

from app.clients.epm_client import EPMClient
from app.services.job_service import JobService


def make_client() -> Mock:
    client = Mock(spec=EPMClient)
    client.application_name = "Vision"
    client.planning_api_root = "HyperionPlanning/rest/v3"
    return client


def test_get_job_returns_typed_status() -> None:
    client = make_client()
    client.get.return_value = {
        "jobId": 224,
        "status": 0,
        "details": "Metadata import was successful",
        "jobName": "Import Account Metadata",
        "descriptiveStatus": "Completed",
        "detailedStatus": 2,
    }

    result = JobService(client).get_job_status(224)

    assert result.job_id == 224
    assert result.is_successful
    assert result.details == "Metadata import was successful"
    client.get.assert_called_once_with(
        "HyperionPlanning/rest/v3/applications/Vision/jobs/224"
    )


def test_get_job_definitions_filters_and_sorts_results() -> None:
    client = make_client()
    client.get.return_value = {
        "items": [
            {
                "jobType": "IMPORT_METADATA",
                "jobName": "Import Entity",
            },
            {
                "jobType": "IMPORT_METADATA",
                "jobName": "Import Account",
            },
        ]
    }

    definitions = JobService(client).get_job_definitions(
        job_type="IMPORT_METADATA"
    )

    assert [definition.job_name for definition in definitions] == [
        "Import Account",
        "Import Entity",
    ]
    client.get.assert_called_once_with(
        (
            "HyperionPlanning/rest/v3/applications/Vision/"
            "jobdefinitions"
        ),
        params={"q": '{"jobType":"IMPORT_METADATA"}'},
    )


def test_get_failure_diagnostics_collects_child_messages() -> None:
    client = make_client()
    client.get.side_effect = [
        {
            "items": [
                {
                    "dimensionName": "Account",
                    "recordsRejected": 1,
                    "links": [
                        {
                            "rel": "child-job-details",
                            "href": (
                                "https://example/HyperionPlanning/rest/v3/"
                                "applications/Vision/jobs/224/childjobs/"
                                "12/details"
                            ),
                        }
                    ],
                }
            ]
        },
        {
            "items": [
                {
                    "msgType": "ERROR",
                    "msgCategory": "Metadata Import",
                    "msgText": "Invalid parent member",
                }
            ]
        },
    ]
    failed_job = Mock(
        job_id=224,
        status=1,
        descriptive_status="Error",
        details="Import failed",
    )

    diagnostics = JobService(client).get_failure_diagnostics(failed_job)

    assert diagnostics.details["items"][0]["recordsRejected"] == 1
    assert diagnostics.messages[0]["msgText"] == "Invalid parent member"
    first_call = client.get.call_args_list[0]
    assert first_call.kwargs["params"]["q"] == '{"messageType":"ERROR"}'
    second_endpoint = client.get.call_args_list[1].args[0]
    assert "/childjobs/12/details" in second_endpoint


def test_get_record_statistics_aggregates_oracle_dimension_details() -> None:
    client = make_client()
    client.get.return_value = {
        "items": [
            {
                "dimensionName": "Account",
                "loadType": "Metadata Import",
                "recordsRead": "1,200",
                "recordsProcessed": 1198,
                "recordsRejected": 2,
            },
            {
                "dimensionName": "Entity",
                "loadType": "Metadata Import",
                "recordsRead": 25,
                "recordsProcessed": "25",
                "recordsRejected": "0",
            },
        ]
    }

    statistics = JobService(client).get_record_statistics(224)

    assert statistics is not None
    assert statistics.records_read == 1225
    assert statistics.records_processed == 1223
    assert statistics.records_rejected == 2
    assert statistics.details[0].dimension_name == "Account"
    assert statistics.to_payload()["source"] == "ORACLE_JOB_DETAILS"
    client.get.assert_called_once_with(
        "HyperionPlanning/rest/v3/applications/Vision/jobs/224/details",
        params={"offset": 0, "limit": 1000},
    )


def test_get_record_statistics_returns_none_when_oracle_has_no_counters() -> None:
    client = make_client()
    client.get.return_value = {
        "items": [{"msgText": "Calculation completed."}]
    }

    assert JobService(client).get_record_statistics(224) is None
