"""Tests for Oracle Data Integration Pipeline REST execution."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.clients.epm_client import EPMClient
from app.services.pipeline_service import PipelineService
from app.utils.exceptions import ConfigurationError, PipelineError
from main import _resolve_pipeline_variables


@pytest.fixture
def client() -> Mock:
    return Mock(spec=EPMClient)


def pipeline_details_response() -> dict:
    return {
        "status": 0,
        "details": None,
        "response": {
            "name": "PIPE01",
            "displayName": "PL_ProductRevenueForecast",
            "parallelJobs": 2,
            "variables": [
                {
                    "varName": "ENDPERIOD",
                    "varDisplayName": "End Period",
                    "varDefaultValue": None,
                    "varType": "LIST",
                    "varSequence": 2,
                    "varDefaultParam": "N",
                },
                {
                    "varName": "STARTPERIOD",
                    "varDisplayName": "Start Period",
                    "varDefaultValue": None,
                    "varType": "LIST",
                    "varSequence": 1,
                    "varDefaultParam": "N",
                },
                {
                    "varName": "IMPORTMODE",
                    "varDisplayName": "Import Mode",
                    "varDefaultValue": "Replace",
                    "varType": "LOOKUP",
                    "varSequence": 3,
                    "varDefaultParam": "N",
                },
                {
                    "varName": "EXPORTMODE",
                    "varDisplayName": "Export Mode",
                    "varDefaultValue": "Merge",
                    "varType": "LIST",
                    "varSequence": 4,
                    "varDefaultParam": "N",
                },
                {
                    "varName": "SEND_MAIL",
                    "varDisplayName": "Send Mail",
                    "varDefaultValue": "No",
                    "varType": "LOOKUP",
                    "varSequence": 5,
                    "varDefaultParam": "Y",
                },
                {
                    "varName": "SEND_TO",
                    "varDisplayName": "Send To",
                    "varDefaultValue": None,
                    "varType": "TEXT",
                    "varSequence": 6,
                    "varDefaultParam": "N",
                },
                {
                    "varName": "ATTACH_LOGS",
                    "varDisplayName": "Attach Logs",
                    "varDefaultValue": "N",
                    "varType": "LOOKUP",
                    "varSequence": 7,
                    "varDefaultParam": "Y",
                },
            ],
            "stages": [
                {
                    "stageName": "Load",
                    "stageDisplayName": "Load Data",
                    "stageSequence": 1,
                    "stageParallel": "N",
                    "jobs": [
                        {
                            "jobName": "Test_DataLoad",
                            "jobType": "INTEGRATION",
                        }
                    ],
                }
            ],
        },
    }


def test_get_pipeline_details_returns_ordered_definition(
    client: Mock,
) -> None:
    client.get.return_value = pipeline_details_response()

    details = PipelineService(client).get_pipeline_details("PIPE01")

    assert details.code == "PIPE01"
    assert details.display_name == "PL_ProductRevenueForecast"
    assert [variable.name for variable in details.variables] == [
        "STARTPERIOD",
        "ENDPERIOD",
        "IMPORTMODE",
        "EXPORTMODE",
        "SEND_MAIL",
        "SEND_TO",
        "ATTACH_LOGS",
    ]
    assert details.job_count == 1
    client.get.assert_called_once_with(
        "aif/rest/V1/pipeline",
        params={"pipelineName": "PIPE01"},
    )


def test_start_pipeline_sends_dynamic_variables(client: Mock) -> None:
    client.post.return_value = {
        "jobId": 501,
        "status": -1,
        "descriptiveStatus": "Processing",
    }

    submission = PipelineService(client).start_pipeline(
        "PIPE01",
        variables={
            "STARTPERIOD": "Jan-26",
            "ENDPERIOD": "Mar-26",
            "IMPORTMODE": "replace",
            "EXPORTMODE": "merge",
            "SEND_MAIL": "no",
            "ATTACH_LOGS": "no",
        },
    )

    assert submission.job_id == 501
    client.post.assert_called_once_with(
        "aif/rest/V1/jobs",
        payload={
            "jobName": "PIPE01",
            "jobType": "pipeline",
            "variables": {
                "STARTPERIOD": "Jan-26",
                "ENDPERIOD": "Mar-26",
                "IMPORTMODE": "Replace",
                "EXPORTMODE": "Merge",
                "SEND_MAIL": "No",
                "ATTACH_LOGS": "N",
            },
        },
    )


def test_pipeline_native_email_requires_recipient(client: Mock) -> None:
    with pytest.raises(PipelineError, match="SEND_TO"):
        PipelineService(client).start_pipeline(
            "PIPE01",
            variables={"SEND_MAIL": "On Success"},
        )

    client.post.assert_not_called()


def test_noninteractive_variables_are_checked_against_definition(
    client: Mock,
) -> None:
    client.get.return_value = pipeline_details_response()
    details = PipelineService(client).get_pipeline_details("PIPE01")

    with pytest.raises(ConfigurationError, match="UNKNOWN"):
        _resolve_pipeline_variables(
            details,
            {
                "STARTPERIOD": "Jan-26",
                "ENDPERIOD": "Jan-26",
                "UNKNOWN": "value",
            },
            include_defaults=False,
        )


def test_noninteractive_pipeline_requires_period_variables(
    client: Mock,
) -> None:
    client.get.return_value = pipeline_details_response()
    details = PipelineService(client).get_pipeline_details("PIPE01")

    with pytest.raises(ConfigurationError, match="STARTPERIOD"):
        _resolve_pipeline_variables(
            details,
            {},
            include_defaults=False,
        )
