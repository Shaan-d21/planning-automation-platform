"""Tests for generic Pipeline external-file pre-flight planning."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.clients.epm_client import EPMClient
from app.models.pipeline import PipelineDetails
from app.models.pipeline_input import (
    PipelineFileConsumer,
    PipelineFileRequirement,
    PipelineFileSource,
)
from app.services.pipeline_preflight_service import (
    PipelinePreflightService,
    build_pipeline_file_selection,
    local_upload_oracle_reference,
)
from app.utils.exceptions import ConfigurationError


def details_from_live_shape() -> PipelineDetails:
    """Build the relevant shape returned by PIPE01 in Oracle."""
    return PipelineDetails.from_response(
        {
            "name": "PIPE01",
            "displayName": "PL_ProductRevenueForecast",
            "variables": [
                {
                    "varName": "STARTPERIOD",
                    "varDisplayName": "Start Period",
                    "varType": "LIST",
                    "varSequence": 1,
                }
            ],
            "stages": [
                {
                    "stageName": "Calc Revenue Forecast",
                    "stageSequence": 1,
                    "jobs": [
                        {
                            "jobType": "businessRule",
                            "jobName": "Calc_Revenue_Growth",
                            "jobSeq": 1,
                            "parameters": [],
                        }
                    ],
                },
                {
                    "stageName": "Data Maintenance",
                    "stageSequence": 2,
                    "jobs": [
                        {
                            "jobType": "integration",
                            "jobName": "Test_Product_Data_Load",
                            "jobSeq": 1,
                            "parameters": [
                                {
                                    "paramName": "fileName",
                                    "paramValue": (
                                        "Test_Sales_DataLoad_V2.csv"
                                    ),
                                }
                            ],
                        }
                    ],
                },
                {
                    "stageName": "Metadata Maintenance",
                    "stageSequence": 3,
                    "jobs": [
                        {
                            "jobType": "importMetadata",
                            "jobName": "Job_Metadata_Load_Test",
                            "jobSeq": 1,
                            "parameters": [
                                {
                                    "paramName": "importZipFileName",
                                    "paramValue": (
                                        "Product_Metadata_Load.csv"
                                    ),
                                },
                                {
                                    "paramName": "errorFile",
                                    "paramValue": "MetadataErrors.zip",
                                },
                            ],
                        }
                    ],
                },
            ],
        }
    )


def test_discovers_multiple_fixed_inputs_and_ignores_non_file_job() -> None:
    requirements = PipelinePreflightService().discover_file_requirements(
        details_from_live_shape()
    )

    assert [item.key for item in requirements] == [
        "Test_Sales_DataLoad_V2.csv",
        "Product_Metadata_Load.csv",
    ]
    assert requirements[0].variable_name is None
    assert requirements[0].allowed_extensions == frozenset(
        {".csv", ".txt", ".zip"}
    )
    assert requirements[1].allowed_extensions == frozenset(
        {".csv", ".zip"}
    )
    assert requirements[0].consumers[0].stage_name == "Data Maintenance"


def test_file_variable_is_discovered_and_linked_to_consumer() -> None:
    details = PipelineDetails.from_response(
        {
            "name": "PIPE02",
            "displayName": "Variable File Pipeline",
            "variables": [
                {
                    "varName": "DATA_FILE",
                    "varDisplayName": "Data File",
                    "varType": "FILE",
                    "varSequence": 1,
                    "required": "Y",
                }
            ],
            "stages": [
                {
                    "stageName": "Load",
                    "stageSequence": 1,
                    "jobs": [
                        {
                            "jobType": "integration",
                            "jobName": "Load Data",
                            "parameters": [
                                {
                                    "paramName": "fileName",
                                    "paramValue": "$DATA_FILE",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    requirements = PipelinePreflightService().discover_file_requirements(
        details
    )

    assert len(requirements) == 1
    assert requirements[0].key == "DATA_FILE"
    assert requirements[0].variable_name == "DATA_FILE"
    assert requirements[0].required is True
    assert len(requirements[0].consumers) == 1


def test_text_variable_used_as_a_file_parameter_is_also_discovered() -> None:
    details = PipelineDetails.from_response(
        {
            "name": "PIPE04",
            "variables": [
                {
                    "varName": "SOURCE_NAME",
                    "varDisplayName": "Source Name",
                    "varType": "TEXT",
                    "varSequence": 1,
                }
            ],
            "stages": [
                {
                    "stageName": "Load",
                    "jobs": [
                        {
                            "jobType": "integration",
                            "jobName": "Load File",
                            "parameters": [
                                {
                                    "paramName": "fileName",
                                    "paramValue": "$SOURCE_NAME",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    requirements = PipelinePreflightService().discover_file_requirements(
        details
    )

    assert len(requirements) == 1
    assert requirements[0].variable_name == "SOURCE_NAME"


def test_pipeline_without_external_files_returns_empty_requirements() -> None:
    details = PipelineDetails.from_response(
        {
            "name": "PIPE03",
            "displayName": "Rules Only",
            "variables": [],
            "stages": [
                {
                    "stageName": "Calculate",
                    "jobs": [
                        {
                            "jobType": "businessRule",
                            "jobName": "Calculate Revenue",
                            "parameters": [],
                        }
                    ],
                }
            ],
        }
    )

    assert (
        PipelinePreflightService().discover_file_requirements(details)
        == ()
    )


def test_fixed_data_integration_file_rejects_applications_inbox_upload(
    tmp_path,
) -> None:
    local_file = tmp_path / "latest-product-data.csv"
    local_file.write_bytes(b"Product,Amount\nP1,10")
    requirement = (
        PipelinePreflightService()
        .discover_file_requirements(details_from_live_shape())[0]
    )
    with pytest.raises(ConfigurationError, match="fixed Data Integration"):
        build_pipeline_file_selection(
            requirement,
            source=PipelineFileSource.LOCAL_UPLOAD,
            oracle_reference="Test_Sales_DataLoad_V2.csv",
            local_path=local_file,
        )


def test_data_integration_variable_upload_uses_epm_inbox_reference(
    tmp_path,
) -> None:
    local_file = tmp_path / "latest-product-data.csv"
    local_file.write_bytes(b"Product,Amount\nP1,10")
    requirement = PipelineFileRequirement(
        key="DATA_FILE",
        display_name="Data File",
        variable_name="DATA_FILE",
        configured_reference="Test_Sales_DataLoad_V2.csv",
        allowed_extensions=frozenset({".csv", ".txt", ".zip"}),
        consumers=(
            PipelineFileConsumer(
                stage_name="Data Maintenance",
                job_name="Test_Product_Data_Load",
                job_type="integration",
                parameter_name="fileName",
            ),
        ),
    )

    reference = local_upload_oracle_reference(
        requirement,
        local_file,
    )
    selection = build_pipeline_file_selection(
        requirement,
        source=PipelineFileSource.LOCAL_UPLOAD,
        oracle_reference=reference,
        local_path=local_file,
    )
    client = Mock(spec=EPMClient)
    client.post_binary.return_value = {"status": 0}

    PipelinePreflightService().stage_uploads(client, (selection,))

    assert selection.oracle_reference == (
        "#epminbox/latest-product-data.csv"
    )
    assert client.post_binary.call_args.args[0].endswith(
        "latest-product-data.csv/contents"
    )


def test_metadata_variable_upload_uses_plain_applications_inbox_name(
    tmp_path,
) -> None:
    local_file = tmp_path / "latest-product-metadata.csv"
    local_file.write_bytes(b"Product,Alias\nP1,Product 1")
    requirement = PipelineFileRequirement(
        key="METADATA_FILE",
        display_name="Metadata File",
        variable_name="METADATA_FILE",
        configured_reference="Product_Metadata_Load.csv",
        allowed_extensions=frozenset({".csv", ".zip"}),
        consumers=(
            PipelineFileConsumer(
                stage_name="Metadata Maintenance",
                job_name="Job_Metadata_Load_Test",
                job_type="importMetadata",
                parameter_name="importZipFileName",
            ),
        ),
    )

    reference = local_upload_oracle_reference(
        requirement,
        local_file,
    )
    selection = build_pipeline_file_selection(
        requirement,
        source=PipelineFileSource.LOCAL_UPLOAD,
        oracle_reference=reference,
        local_path=local_file,
    )
    client = Mock(spec=EPMClient)
    client.post_binary.return_value = {"status": 0}

    PipelinePreflightService().stage_uploads(client, (selection,))

    assert selection.oracle_reference == "latest-product-metadata.csv"
    assert client.post_binary.call_args.args[0].endswith(
        "latest-product-metadata.csv/contents"
    )
