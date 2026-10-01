"""Tests for Data Integration period and REST execution behavior."""

from __future__ import annotations

from unittest.mock import Mock, call

import pytest

from app.application.operations import _data_integration_upload_target
from app.clients.epm_client import EPMClient
from app.models.data_integration import DataIntegrationPeriodRange
from app.models.data_integration import DataIntegrationFileReference
from app.services.data_integration_service import DataIntegrationService
from app.utils.exceptions import DataIntegrationError, OperationError
from app.web.schemas import DataIntegrationRunRequest


def three_period_range() -> DataIntegrationPeriodRange:
    return DataIntegrationPeriodRange.from_period_names(
        "Jun-19",
        "Aug-19",
        expected_period_count=3,
    )


def test_default_upload_uses_epminbox_reference() -> None:
    reference = DataIntegrationFileReference.from_default_upload(
        "Test_Product_Revenue_DataLoad.csv"
    )

    assert str(reference) == (
        "#epminbox/Test_Product_Revenue_DataLoad.csv"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Test.csv", "Test.csv"),
        ("inbox/Test.csv", "inbox/Test.csv"),
        ("#epminbox/Test.csv", "#epminbox/Test.csv"),
        ("epminbox/Test.csv", "#epminbox/Test.csv"),
    ],
)
def test_existing_file_reference_preserves_oracle_location(
    value: str,
    expected: str,
) -> None:
    assert str(DataIntegrationFileReference.from_existing(value)) == expected


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (None, ("latest.csv", None, "#epminbox/latest.csv")),
        (
            "Configured.csv",
            ("Configured.csv", None, "#epminbox/Configured.csv"),
        ),
        (
            "#epminbox/Configured.csv",
            ("Configured.csv", None, "#epminbox/Configured.csv"),
        ),
        (
            "inbox/monthly/Configured.csv",
            (
                "Configured.csv",
                "inbox/monthly",
                "inbox/monthly/Configured.csv",
            ),
        ),
    ],
)
def test_upload_target_resolves_oracle_location(target, expected) -> None:
    assert _data_integration_upload_target("latest.csv", target) == expected


def test_upload_target_rejects_epminbox_subfolders() -> None:
    with pytest.raises(OperationError, match="root Applications Inbox"):
        _data_integration_upload_target(
            "latest.csv",
            "#epminbox/monthly/latest.csv",
        )


def test_run_request_normalizes_bare_upload_target() -> None:
    request = DataIntegrationRunRequest(
        integration_name="Forecast Load",
        start_period="Jan#FY27",
        end_period="Mar#FY27",
        upload_token="upload-token",
        upload_target="Configured.csv",
    )

    assert request.upload_target == "#epminbox/Configured.csv"


def test_period_range_builds_oracle_parameter_and_column_periods() -> None:
    period_range = DataIntegrationPeriodRange.from_user_values(
        6,
        8,
        2019,
        expected_period_count=3,
    )

    assert period_range.start_period == "Jun-19"
    assert period_range.end_period == "Aug-19"
    assert period_range.oracle_period_name == "{Jun-19}{Aug-19}"
    assert period_range.periods == ("Jun-19", "Jul-19", "Aug-19")


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        ("Jun-19", "Jul-19", "expects 3 periods"),
        ("Aug-19", "Jun-19", "earlier"),
    ],
)
def test_period_range_rejects_unsafe_ranges(
    start: str,
    end: str,
    message: str,
) -> None:
    with pytest.raises(DataIntegrationError, match=message):
        DataIntegrationPeriodRange.from_period_names(
            start,
            end,
            expected_period_count=3,
        )


def test_period_range_supports_cross_year_and_variable_counts() -> None:
    period_range = DataIntegrationPeriodRange.from_period_names(
        "Dec-19",
        "Feb-20",
    )

    assert period_range.period_count == 3
    assert period_range.periods == ("Dec-19", "Jan-20", "Feb-20")
    assert period_range.oracle_period_name == "{Dec-19}{Feb-20}"


def test_period_range_preserves_planning_member_notation() -> None:
    period_range = DataIntegrationPeriodRange.from_period_names(
        "Jun#FY19",
        "Aug#FY19",
    )

    assert period_range.period_count is None
    assert period_range.oracle_period_name == "{Jun#FY19}{Aug#FY19}"


def test_rest_service_submits_documented_integration_payload() -> None:
    client = Mock(spec=EPMClient)
    client.post.return_value = {
        "jobId": 1234,
        "status": -1,
        "jobStatus": "RUNNING",
    }
    service = DataIntegrationService(client)

    submission = service.start_integration(
        "#epminbox/Test_Sales_DataLoad_V2.csv",
        "Test_DataLoad",
        three_period_range(),
        import_mode="replace",
        export_mode="merge",
    )

    assert submission.job_id == 1234
    assert submission.period_name == "{Jun-19}{Aug-19}"
    client.post.assert_called_once_with(
        "aif/rest/V1/jobs",
        payload={
            "jobType": "INTEGRATION",
            "jobName": "Test_DataLoad",
            "periodName": "{Jun-19}{Aug-19}",
            "importMode": "Replace",
            "exportMode": "Merge",
            "fileName": "#epminbox/Test_Sales_DataLoad_V2.csv",
        },
    )


def test_rest_service_retrieves_data_integration_job_status() -> None:
    client = Mock(spec=EPMClient)
    client.get.return_value = {
        "jobId": 1234,
        "status": 0,
        "jobStatus": "SUCCESS",
    }

    job = DataIntegrationService(client).get_job_status(1234)

    assert job.is_successful
    assert job.descriptive_status == "SUCCESS"
    assert client.get.call_args_list == [
        call("aif/rest/V1/jobs/1234")
    ]


def test_rest_service_does_not_restrict_source_file_extension() -> None:
    client = Mock(spec=EPMClient)
    client.post.return_value = {"jobId": 9, "status": -1}

    submission = DataIntegrationService(client).start_integration(
        "FlexibleLayout.dat",
        "Delimited_Data_Load",
        DataIntegrationPeriodRange.from_period_names("Jun-19", "Jun-19"),
    )

    assert submission.file_name == "FlexibleLayout.dat"
    assert (
        client.post.call_args.kwargs["payload"]["fileName"]
        == "FlexibleLayout.dat"
    )


def test_rest_service_uses_file_configured_in_integration() -> None:
    client = Mock(spec=EPMClient)
    client.post.return_value = {"jobId": 10, "status": -1}

    submission = DataIntegrationService(client).start_integration(
        None,
        "Configured_File_Load",
        DataIntegrationPeriodRange.from_period_names("Jun-19", "Jun-19"),
    )

    assert submission.file_name is None
    payload = client.post.call_args.kwargs["payload"]
    assert "fileName" not in payload


def test_rest_service_surfaces_oracle_submission_detail_without_job_id() -> None:
    client = Mock(spec=EPMClient)
    client.post.return_value = {
        "status": 4,
        "jobStatus": "FAILED",
        "details": "Invalid periodName {Jan}{Mar}.",
    }

    with pytest.raises(
        DataIntegrationError,
        match=r"Invalid periodName \{Jan\}\{Mar\}",
    ):
        DataIntegrationService(client).start_integration(
            None,
            "Product_Load",
            DataIntegrationPeriodRange.from_period_names("Jan", "Mar"),
        )


def test_rest_service_explains_empty_submission_response() -> None:
    client = Mock(spec=EPMClient)
    client.post.return_value = {"status": 0, "items": []}

    with pytest.raises(
        DataIntegrationError,
        match="returned no process ID",
    ):
        DataIntegrationService(client).start_integration(
            None,
            "Product_Load",
            DataIntegrationPeriodRange.from_period_names("Jan-27", "Mar-27"),
        )


def test_rest_service_rejects_unsupported_mode() -> None:
    with pytest.raises(DataIntegrationError, match="Unsupported"):
        DataIntegrationService.normalize_export_mode("Delete everything")
