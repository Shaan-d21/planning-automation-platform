"""Tests for shared governed-action input contracts."""

from __future__ import annotations

import pytest

from app.application.action_inputs import (
    action_input_schema,
    action_input_schema_payload,
    missing_required_inputs,
    normalize_action_inputs,
)
from app.utils.exceptions import ConfigurationError


def test_process_schema_requires_planning_year() -> None:
    schema = action_input_schema("process", "MONTHLY_FORECAST")

    assert [item.key for item in missing_required_inputs(schema, {})] == [
        "planning_year"
    ]
    values = normalize_action_inputs(
        schema,
        {
            "planning_year": " FY27 ",
            "start_period": " Jan ",
            "end_period": "Mar",
        },
    )

    assert values == {
        "planning_year": "FY27",
        "start_period": "Jan",
        "end_period": "Mar",
    }
    assert missing_required_inputs(schema, values) == ()


def test_process_period_range_must_be_complete_when_used() -> None:
    schema = action_input_schema("process", "MONTHLY_FORECAST")
    values = normalize_action_inputs(
        schema,
        {"planning_year": "FY27", "start_period": "Jan"},
    )

    assert [item.key for item in missing_required_inputs(schema, values)] == [
        "end_period"
    ]


def test_false_is_a_valid_required_boolean() -> None:
    schema = action_input_schema("operation", "data-maps")
    values = normalize_action_inputs(schema, {"clear_target": False})

    assert values["clear_target"] is False
    assert missing_required_inputs(schema, values) == ()


def test_unknown_inputs_and_invalid_choices_are_rejected() -> None:
    schema = action_input_schema("operation", "data-integrations")

    with pytest.raises(ConfigurationError, match="Unsupported action inputs"):
        normalize_action_inputs(schema, {"password": "secret"})
    with pytest.raises(ConfigurationError, match="Import mode must be one of"):
        normalize_action_inputs(schema, {"import_mode": "Delete everything"})


def test_existing_inbox_source_requires_a_file_reference() -> None:
    schema = action_input_schema("operation", "data-import")
    values = normalize_action_inputs(
        schema,
        {"file_source": "Existing Oracle Inbox file", "inbox_file": ""},
    )

    assert [item.key for item in missing_required_inputs(schema, values)] == [
        "inbox_file"
    ]


def test_data_import_schema_accepts_governed_upload_receipt_fields() -> None:
    schema = action_input_schema("operation", "data-import")

    values = normalize_action_inputs(
        schema,
        {
            "file_source": "Upload on governed screen",
            "inbox_file": "",
            "upload_token": "upload-token-1",
            "upload_name": "Forecast.csv",
            "error_file_name": "Forecast_Errors.log",
        },
    )

    assert values["upload_token"] == "upload-token-1"
    assert values["upload_name"] == "Forecast.csv"
    assert values["error_file_name"] == "Forecast_Errors.log"


def test_metadata_import_schema_accepts_upload_and_refresh_fields() -> None:
    schema = action_input_schema("operation", "metadata-import")

    values = normalize_action_inputs(
        schema,
        {
            "file_source": "Upload on governed screen",
            "inbox_file": "",
            "upload_token": "metadata-upload-1",
            "upload_name": "Products.csv",
            "error_file_name": "Metadata_Errors.csv",
            "refresh_after_import": True,
            "refresh_job_name": "Refresh_Cube",
        },
    )

    assert values["upload_name"] == "Products.csv"
    assert values["refresh_after_import"] is True
    assert values["refresh_job_name"] == "Refresh_Cube"


def test_schema_payload_is_json_ready() -> None:
    payload = action_input_schema_payload(
        action_input_schema("operation", "data-integrations")
    )

    assert payload[0]["kind"] == "text"
    assert isinstance(payload[2]["options"], list)
