"""Tests for standalone Oracle EPM operation use cases."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, call, patch

from app.application.operations import (
    BusinessRuleOperationInput,
    CubeRefreshOperationInput,
    DataImportOperationInput,
    DataIntegrationOperationInput,
    DataMapOperationInput,
    MetadataImportOperationInput,
    OperationCatalogService,
    OperationCommandExecutor,
    PipelineOperationInput,
)
from app.application.substitution_variables import (
    SubstitutionVariableAction,
    SubstitutionVariableChangeResult,
    SubstitutionVariableOperationInput,
)
from app.application.reports import (
    ReportGenerationOperationInput,
    ReportGenerationResult,
)
from app.config.settings import Settings
from app.models.business_rule import BusinessRuleSubmission
from app.models.data_map import DataMapExecutionRequest, DataMapSubmission
from app.models.cube_refresh import CubeRefreshSubmission
from app.models.job import (
    JobDefinition,
    JobRecordStatistics,
    JobRecordStatisticsItem,
    JobResult,
)
from app.models.environment import PlanTypeInfo
from app.models.metadata_job import (
    MetadataImportMode,
    MetadataJobSubmission,
)
from app.models.pipeline import (
    PipelineDetails,
    PipelineJob,
    PipelineJobParameter,
    PipelineStage,
    PipelineSubmission,
    PipelineVariable,
)
from app.models.oracle_artifact import (
    OracleArtifactStatus,
    OracleArtifactType,
)
from app.models.data_integration import DataIntegrationSubmission
from app.models.data_job import DataJobSubmission
from app.models.workflow import WorkflowStatus, WorkflowStepStatus
from app.utils.exceptions import APIRequestError, EPMConnectionError


def _settings(tmp_path: Path) -> Settings:
    password_file = tmp_path / "password.epw"
    password_file.write_text("encrypted", encoding="utf-8")
    pipeline_catalog = tmp_path / "pipelines.json"
    pipeline_catalog.write_text(
        json.dumps(
            {
                "pipelines": [
                    {
                        "code": "PIPE01",
                        "name": "Forecast Pipeline",
                        "description": "Monthly forecast",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    integration_catalog = tmp_path / "integrations.json"
    integration_catalog.write_text(
        json.dumps(
            {
                "integrations": [
                    {
                        "name": "Forecast Load",
                        "description": "Forecast data",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="administrator",
        epm_password="secret",
        application_name="Vision",
        workflow_database_file=tmp_path / "history.sqlite3",
        pipeline_catalog_file=pipeline_catalog,
        data_integration_catalog_file=integration_catalog,
        default_poll_interval=0.01,
        default_job_timeout=2,
        epm_automate_password_file=password_file,
    )


@patch("app.application.operations.JobService")
@patch("app.application.operations.EPMClient")
def test_catalog_discovers_live_rules_and_data_maps(
    client_class: Mock,
    job_service_class: Mock,
    tmp_path: Path,
) -> None:
    client_class.return_value.__enter__.return_value = Mock(
        application_name="Vision",
        planning_api_root="rest/v3",
    )
    job_service_class.return_value.get_job_definitions.side_effect = (
        (
            JobDefinition(job_name="Calculate Revenue", job_type="RULES"),
        ),
        (
            JobDefinition(
                job_name="Revenue to Reporting",
                job_type="PLAN_TYPE_MAP",
            ),
        ),
        (
            JobDefinition(
                job_name="Import Products",
                job_type="IMPORT_METADATA",
            ),
        ),
        (
            JobDefinition(
                job_name="Import Forecast Data",
                job_type="IMPORT_DATA",
            ),
        ),
        (
            JobDefinition(
                job_name="Refresh_Cube",
                job_type="CUBE_REFRESH",
            ),
        ),
    )

    catalog = OperationCatalogService(_settings(tmp_path)).discover()

    assert catalog.business_rules == ("Calculate Revenue",)
    assert catalog.data_maps == ("Revenue to Reporting",)
    assert {item.code for item in catalog.operations} == {
        "business-rules",
        "data-maps",
        "pipelines",
        "data-integrations",
        "metadata-import",
        "data-import",
        "substitution-variables",
        "user-variables",
        "cube-refresh",
        "report-generation",
    }
    assert catalog.pipelines[0].code == "PIPE01"
    assert catalog.data_integrations[0].name == "Forecast Load"
    assert catalog.metadata_jobs == ("Import Products",)
    assert catalog.data_import_jobs == ("Import Forecast Data",)
    assert catalog.cube_refresh_jobs == ("Refresh_Cube",)
    assert catalog.oracle_available is True
    assert catalog.oracle_message is None


@patch("app.application.operations.JobService")
@patch("app.application.operations.EPMClient")
def test_catalog_can_discover_only_cube_refresh_jobs(
    client_class: Mock,
    job_service_class: Mock,
    tmp_path: Path,
) -> None:
    client_class.return_value.__enter__.return_value = Mock(
        application_name="Vision",
        planning_api_root="rest/v3",
    )
    job_service_class.return_value.get_job_definitions.side_effect = (
        (
            JobDefinition(
                job_name="RefreshCube",
                job_type="CUBE_REFRESH",
            ),
        ),
        (
            JobDefinition(
                job_name="Refresh_Cube",
                job_type="Cube Refresh",
            ),
            JobDefinition(
                job_name="Calculate Revenue",
                job_type="RULES",
            ),
        ),
    )

    jobs = OperationCatalogService(_settings(tmp_path)).discover_job_names(
        job_type="CUBE_REFRESH"
    )

    assert jobs == ("Refresh_Cube",)
    assert job_service_class.return_value.get_job_definitions.call_args_list == [
        call(job_type="CUBE_REFRESH"),
        call(),
    ]


@patch("app.application.operations.EPMClient")
def test_catalog_keeps_registered_operations_when_oracle_is_unavailable(
    client_class: Mock,
    tmp_path: Path,
) -> None:
    client = client_class.return_value.__enter__.return_value
    client.authenticate.side_effect = EPMConnectionError(
        "Request to Oracle EPM timed out after 30 seconds."
    )

    catalog = OperationCatalogService(_settings(tmp_path)).discover()

    assert catalog.business_rules == ()
    assert catalog.data_maps == ()
    assert catalog.pipelines[0].code == "PIPE01"
    assert catalog.data_integrations[0].name == "Forecast Load"
    assert catalog.oracle_available is False
    assert "currently unavailable" in (catalog.oracle_message or "")


@patch("app.application.operations.EPMClient")
def test_registered_catalog_does_not_contact_oracle(
    client_class: Mock,
    tmp_path: Path,
) -> None:
    catalog = OperationCatalogService(_settings(tmp_path)).discover_registered()

    client_class.assert_not_called()
    assert catalog.pipelines[0].code == "PIPE01"
    assert catalog.data_integrations[0].name == "Forecast Load"


def _pipeline_details() -> PipelineDetails:
    return PipelineDetails(
        code="PIPE01",
        display_name="Forecast Pipeline",
        parallel_jobs=None,
        variables=(
            PipelineVariable(
                name="STARTPERIOD",
                display_name="Start Period",
                default_value=None,
                variable_type="TEXT",
                value_object=None,
                sequence=1,
                is_default_parameter=False,
                is_required=True,
            ),
            PipelineVariable(
                name="DATA_FILE",
                display_name="Data File",
                default_value="#epminbox/forecast.csv",
                variable_type="FILE",
                value_object=None,
                sequence=2,
                is_default_parameter=False,
                is_required=True,
            ),
        ),
        stages=(
            PipelineStage(
                name="LOAD",
                display_name="Load forecast",
                sequence=1,
                runs_in_parallel=False,
                jobs=(
                    PipelineJob(
                        name="Forecast Load",
                        job_type="Integration",
                        sequence=1,
                        parameters=(
                            PipelineJobParameter(
                                name="File Name",
                                value="$DATA_FILE",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


@patch("app.application.operations.PipelineService")
@patch("app.application.operations.EPMClient")
def test_catalog_sync_verifies_pipeline_and_discovers_integrations(
    client_class: Mock,
    pipeline_service_class: Mock,
    tmp_path: Path,
) -> None:
    client_class.return_value.__enter__.return_value = Mock(
        application_name="Vision",
        planning_api_root="rest/v3",
    )
    pipeline_service_class.return_value.get_pipeline_details.return_value = (
        _pipeline_details()
    )
    service = OperationCatalogService(_settings(tmp_path))

    result = service.synchronize_artifacts()
    integrations = service.registered_artifacts(
        OracleArtifactType.DATA_INTEGRATION,
    )

    assert result.oracle_available is True
    assert result.verified_pipelines == 1
    assert result.discovered_integrations == 1
    assert result.verified_integrations == 1
    assert result.missing_integrations == 0
    assert next(
        item for item in integrations if item.oracle_identifier == "Forecast Load"
    ).status == OracleArtifactStatus.VERIFIED


@patch("app.application.operations.ApplicationService")
@patch("app.application.operations.JobService")
@patch("app.application.operations.PipelineService")
@patch("app.application.operations.EPMClient")
def test_catalog_sync_discovers_all_supported_jobs_and_cubes_in_one_session(
    client_class: Mock,
    pipeline_service_class: Mock,
    job_service_class: Mock,
    application_service_class: Mock,
    tmp_path: Path,
) -> None:
    client_class.return_value.__enter__.return_value = Mock()
    pipeline_service_class.return_value.get_pipeline_details.return_value = (
        _pipeline_details()
    )
    job_service_class.return_value.get_job_definitions.side_effect = (
        lambda *, job_type: (JobDefinition(f"{job_type} Job", job_type),)
    )
    application_service_class.return_value.get_plan_types.return_value = (
        PlanTypeInfo(name="Plan1", cube_name="Plan1"),
        PlanTypeInfo(name="Reporting", cube_name="Reporting"),
    )
    service = OperationCatalogService(_settings(tmp_path))

    result = service.synchronize_artifacts()

    assert client_class.return_value.__enter__.call_count == 1
    assert result.verified_business_rules == 1
    assert result.verified_data_maps == 1
    assert result.verified_metadata_jobs == 1
    assert result.verified_data_import_jobs == 1
    assert result.verified_cube_refresh_jobs == 1
    assert result.verified_cubes == 2
    assert result.verification_errors == 0
    assert {
        item.artifact_type for item in service.synchronized_catalog()
    }.issuperset(
        {
            OracleArtifactType.BUSINESS_RULE,
            OracleArtifactType.DATA_MAP,
            OracleArtifactType.METADATA_IMPORT_JOB,
            OracleArtifactType.DATA_IMPORT_JOB,
            OracleArtifactType.CUBE_REFRESH_JOB,
            OracleArtifactType.CUBE,
        }
    )


@patch("app.application.operations.PipelineService")
@patch("app.application.operations.EPMClient")
def test_catalog_sync_hides_definitively_invalid_data_integration(
    client_class: Mock,
    pipeline_service_class: Mock,
    tmp_path: Path,
) -> None:
    client_class.return_value.__enter__.return_value = Mock(
        application_name="Vision",
        planning_api_root="rest/v3",
    )
    pipeline_service_class.return_value.get_pipeline_details.return_value = (
        _pipeline_details()
    )
    service = OperationCatalogService(_settings(tmp_path))
    service.register_data_integration("Removed_Load")
    service._artifact_registry().record_verification_error(
        OracleArtifactType.DATA_INTEGRATION,
        "Removed_Load",
        "EPMFDM-140149: Invalid Integration Name: Removed_Load",
    )

    result = service.synchronize_artifacts()
    integrations = service.registered_artifacts(
        OracleArtifactType.DATA_INTEGRATION,
        include_inactive=True,
    )
    removed = next(
        item for item in integrations if item.oracle_identifier == "Removed_Load"
    )

    assert removed.status == OracleArtifactStatus.MISSING
    assert result.missing_integrations == 1
    assert "Removed_Load" not in {
        item.name for item in service.discover_registered().data_integrations
    }


@patch("app.application.operations.PipelineService")
@patch("app.application.operations.EPMClient")
def test_catalog_sync_soft_deactivates_only_after_two_definitive_misses(
    client_class: Mock,
    pipeline_service_class: Mock,
    tmp_path: Path,
) -> None:
    client_class.return_value.__enter__.return_value = Mock(
        application_name="Vision",
        planning_api_root="rest/v3",
    )
    pipeline_service_class.return_value.get_pipeline_details.side_effect = (
        APIRequestError("HTTP 404: Not Found", status_code=404)
    )
    service = OperationCatalogService(_settings(tmp_path))

    service.synchronize_artifacts()
    first = service.registered_artifacts(
        OracleArtifactType.PIPELINE,
        include_inactive=True,
    )[0]
    service.synchronize_artifacts()
    second = service.registered_artifacts(
        OracleArtifactType.PIPELINE,
        include_inactive=True,
    )[0]

    assert first.status == OracleArtifactStatus.MISSING
    assert first.is_active is True
    assert second.status == OracleArtifactStatus.INACTIVE
    assert second.is_active is False


def test_standalone_integration_registration_waits_for_governed_run(
    tmp_path: Path,
) -> None:
    service = OperationCatalogService(_settings(tmp_path))

    artifact = service.register_data_integration("Standalone_Load")

    assert artifact.status == OracleArtifactStatus.PENDING
    assert service.require_data_integration("Standalone_Load").is_runnable


def test_invalid_oracle_artifact_name_is_a_definitive_missing_result() -> None:
    error = APIRequestError(
        "Error in fetching the pipeline obj PL02 :: Pipeline name is invalid.",
        status_code=400,
    )

    assert OperationCatalogService._is_missing_artifact_error(error) is True


def test_switching_oracle_application_does_not_reuse_verified_catalog(
    tmp_path: Path,
) -> None:
    first_settings = _settings(tmp_path)
    first = OperationCatalogService(first_settings).discover_registered()
    second_settings = replace(first_settings, application_name="EBPCS")
    second_service = OperationCatalogService(second_settings)
    second = second_service.discover_registered()

    assert first.pipelines[0].code == "PIPE01"
    assert first.data_integrations[0].name == "Forecast Load"
    assert second.pipelines == ()
    assert second.data_integrations == ()
    assert {
        item.status
        for item in second_service.registered_artifacts(
            OracleArtifactType.PIPELINE,
        )
    } == {OracleArtifactStatus.PENDING}


@patch("app.application.operations.PipelineService")
@patch("app.application.operations.EPMClient")
def test_pipeline_preflight_returns_live_variables_stages_and_files(
    client_class: Mock,
    pipeline_service_class: Mock,
    tmp_path: Path,
) -> None:
    client_class.return_value.__enter__.return_value = Mock()
    pipeline_service_class.validate_pipeline_code.return_value = "PIPE01"
    pipeline_service_class.return_value.get_pipeline_details.return_value = (
        _pipeline_details()
    )

    preview = OperationCatalogService(
        _settings(tmp_path)
    ).preflight_pipeline("PIPE01")

    assert preview.code == "PIPE01"
    assert [item.name for item in preview.variables] == ["STARTPERIOD"]
    assert preview.file_requirements[0].key == "DATA_FILE"
    assert preview.file_requirements[0].allowed_extensions == (
        ".csv",
        ".txt",
        ".zip",
    )
    assert preview.stages[0].job_count == 1


@patch("app.application.operations.create_notification_service")
@patch("app.application.operations.JobMonitor")
@patch("app.application.operations.BusinessRuleService.start_rule")
@patch("app.application.operations.EPMClient")
def test_business_rule_execution_is_durable_and_monitored(
    client_class: Mock,
    start_rule: Mock,
    monitor_class: Mock,
    notification_factory: Mock,
    tmp_path: Path,
) -> None:
    client_class.return_value.application_name = "Vision"
    client_class.return_value.planning_api_root = (
        "https://example.oraclecloud.com/rest/v3"
    )
    start_rule.return_value = BusinessRuleSubmission(
        job_id=51,
        rule_name="Calculate Revenue",
        runtime_prompts=(("Volume", "100"),),
    )
    monitor_class.return_value.wait_for_completion.return_value = JobResult(
        job_id=51,
        status=0,
        descriptive_status="Completed",
    )

    run = OperationCommandExecutor(_settings(tmp_path)).execute(
        BusinessRuleOperationInput(
            rule_name="Calculate Revenue",
            runtime_prompts={"Volume": "100"},
        ),
        execution_id="rule-run-1",
        log_file=tmp_path / "rule.log",
    )

    assert run.status is WorkflowStatus.SUCCESS
    assert [step.status for step in run.steps] == [
        WorkflowStepStatus.SUCCESS,
        WorkflowStepStatus.SUCCESS,
        WorkflowStepStatus.SUCCESS,
    ]
    assert run.steps[-1].details["job_id"] == 51
    start_rule.assert_called_once_with(
        "Calculate Revenue",
        runtime_prompts={"Volume": "100"},
    )
    client_class.return_value.close.assert_called_once_with()
    notification_factory.return_value.publish.assert_called_once()


@patch("app.application.operations.create_notification_service")
@patch("app.application.operations.JobMonitor")
@patch("app.application.operations.DataMapService.start_data_map")
@patch("app.application.operations.EPMClient")
def test_data_map_execution_preserves_clear_and_pov_controls(
    client_class: Mock,
    start_data_map: Mock,
    monitor_class: Mock,
    notification_factory: Mock,
    tmp_path: Path,
) -> None:
    client_class.return_value.application_name = "Vision"
    client_class.return_value.planning_api_root = (
        "https://example.oraclecloud.com/rest/v3"
    )
    start_data_map.return_value = DataMapSubmission(
        job_id=77,
        request=DataMapExecutionRequest(
            data_map_name="Revenue to Reporting",
            clear_target=True,
        ),
    )
    monitor_class.return_value.wait_for_completion.return_value = JobResult(
        job_id=77,
        status=0,
        descriptive_status="Completed",
    )

    run = OperationCommandExecutor(_settings(tmp_path)).execute(
        DataMapOperationInput(
            data_map_name="Revenue to Reporting",
            clear_target=True,
            member_overrides={"Year": "FY26"},
            exclusion_overrides={"Entity": "No Entity"},
        ),
        execution_id="map-run-1",
        log_file=tmp_path / "map.log",
    )

    assert run.status is WorkflowStatus.SUCCESS
    assert run.steps[-1].details["job_id"] == 77
    start_data_map.assert_called_once_with(
        "Revenue to Reporting",
        clear_target=True,
        member_overrides={"Year": "FY26"},
        exclusion_overrides={"Entity": "No Entity"},
    )
    notification_factory.return_value.publish.assert_called_once()


@patch("app.application.operations.create_notification_service")
@patch("app.application.operations.JobMonitor")
@patch("app.application.operations.PipelinePreflightService.stage_uploads")
@patch("app.application.operations.PipelineService.start_pipeline")
@patch("app.application.operations.PipelineService.get_pipeline_details")
@patch("app.application.operations.EPMClient")
def test_pipeline_execution_merges_files_and_runtime_variables(
    client_class: Mock,
    get_pipeline_details: Mock,
    start_pipeline: Mock,
    stage_uploads: Mock,
    monitor_class: Mock,
    notification_factory: Mock,
    tmp_path: Path,
) -> None:
    client_class.return_value.application_name = "Vision"
    client_class.return_value.planning_api_root = (
        "https://example.oraclecloud.com/rest/v3"
    )
    get_pipeline_details.return_value = _pipeline_details()
    start_pipeline.return_value = PipelineSubmission(
        job_id=91,
        pipeline_code="PIPE01",
        variables=(),
    )
    monitor_class.return_value.wait_for_completion.return_value = JobResult(
        job_id=91,
        status=0,
        descriptive_status="Completed",
    )

    run = OperationCommandExecutor(_settings(tmp_path)).execute(
        PipelineOperationInput(
            pipeline_code="PIPE01",
            variables={"STARTPERIOD": "Jan-26"},
            uploads={},
            inbox_files={"DATA_FILE": "#epminbox/new.csv"},
        ),
        execution_id="pipeline-run-1",
        log_file=tmp_path / "pipeline.log",
    )

    assert run.status is WorkflowStatus.SUCCESS
    variables = start_pipeline.call_args.kwargs["variables"]
    assert variables == {
        "STARTPERIOD": "Jan-26",
        "DATA_FILE": "#epminbox/new.csv",
    }
    stage_uploads.assert_called_once()
    notification_factory.return_value.publish.assert_called_once()


@patch("app.application.operations.create_notification_service")
@patch("app.application.operations.JobMonitor")
@patch("app.application.operations.DataIntegrationService.start_integration")
@patch("app.application.operations.EPMClient")
def test_data_integration_execution_preserves_inbox_and_period_range(
    client_class: Mock,
    start_integration: Mock,
    monitor_class: Mock,
    notification_factory: Mock,
    tmp_path: Path,
) -> None:
    client_class.return_value.application_name = "Vision"
    client_class.return_value.planning_api_root = (
        "https://example.oraclecloud.com/rest/v3"
    )
    client_class.return_value.get_binary.return_value = (
        b"Total records read: 100\n"
        b"Total records processed: 98\n"
        b"Total records rejected: 2\n"
    )
    start_integration.return_value = DataIntegrationSubmission(
        job_id=101,
        integration_name="Forecast Load",
        file_name="#epminbox/forecast.csv",
        period_name="{Jan-26}{Mar-26}",
        import_mode="Replace",
        export_mode="Merge",
    )
    monitor_class.return_value.wait_for_completion.return_value = JobResult(
        job_id=101,
        status=0,
        descriptive_status="Completed",
        raw_response={
            "jobId": 101,
            "status": 0,
            "jobStatus": "SUCCESS",
            "logFileName": "outbox/logs/Forecast_Load_101.log",
        },
    )

    run = OperationCommandExecutor(_settings(tmp_path)).execute(
        DataIntegrationOperationInput(
            integration_name="Forecast Load",
            start_period="Jan-26",
            end_period="Mar-26",
            import_mode="Replace",
            export_mode="Merge",
            inbox_file="#epminbox/forecast.csv",
        ),
        execution_id="integration-run-1",
        log_file=tmp_path / "integration.log",
    )

    assert run.status is WorkflowStatus.SUCCESS
    call = start_integration.call_args
    assert call.args[0] == "#epminbox/forecast.csv"
    assert call.args[2].oracle_period_name == "{Jan-26}{Mar-26}"
    assert call.kwargs == {
        "import_mode": "Replace",
        "export_mode": "Merge",
    }
    execution_step = next(
        step for step in run.steps if step.details.get("job_id") == 101
    )
    assert execution_step.details["record_statistics"] == {
        "source": "ORACLE_DATA_INTEGRATION_LOG",
        "records_read": 100,
        "records_processed": 98,
        "records_rejected": 2,
        "details": [
            {
                "dimension_name": None,
                "load_type": "Data Integration",
                "records_read": 100,
                "records_processed": 98,
                "records_rejected": 2,
            }
        ],
    }
    notification_factory.return_value.publish.assert_called_once()


@patch("app.application.operations.create_notification_service")
@patch("app.application.operations.JobMonitor")
@patch("app.application.operations.CubeRefreshService.start_refresh")
@patch("app.application.operations.MetadataService.start_import")
@patch("app.application.operations.EPMClient")
def test_metadata_import_can_refresh_after_success(
    client_class: Mock,
    start_import: Mock,
    start_refresh: Mock,
    monitor_class: Mock,
    notification_factory: Mock,
    tmp_path: Path,
) -> None:
    client_class.return_value.application_name = "Vision"
    client_class.return_value.planning_api_root = (
        "https://example.oraclecloud.com/rest/v3"
    )
    start_import.return_value = MetadataJobSubmission(
        job_id=201,
        job_name="Import Products",
        file_name="Products.csv",
        import_mode=MetadataImportMode.JOB_DEFINITION,
    )
    start_refresh.return_value = CubeRefreshSubmission(
        job_id=202,
        job_name="RefreshCube",
    )
    monitor_class.return_value.wait_for_completion.side_effect = (
        JobResult(
            job_id=201,
            status=0,
            descriptive_status="Completed",
        ),
        JobResult(
            job_id=202,
            status=0,
            descriptive_status="Completed",
        ),
    )

    run = OperationCommandExecutor(_settings(tmp_path)).execute(
        MetadataImportOperationInput(
            job_name="Import Products",
            inbox_file="Products.csv",
            error_file_name="Products_Errors.csv",
            refresh_job_name="RefreshCube",
        ),
        execution_id="metadata-run-1",
        log_file=tmp_path / "metadata.log",
    )

    assert run.status is WorkflowStatus.SUCCESS
    assert [step.name for step in run.steps] == [
        "Validate operation inputs",
        "Connect to Oracle EPM",
        "Execute Metadata Import",
        "Refresh Planning Cube",
    ]
    start_import.assert_called_once_with(
        "Products.csv",
        "Import Products",
        error_file_name="Products_Errors-metadata.csv",
    )
    start_refresh.assert_called_once_with("RefreshCube")
    notification_factory.return_value.publish.assert_called_once()


@patch("app.application.operations.create_notification_service")
@patch("app.application.operations.JobMonitor")
@patch("app.application.operations.DataService.start_import")
@patch("app.application.operations.JobService")
@patch("app.application.operations.EPMClient")
def test_native_data_import_reuses_an_inbox_file(
    client_class: Mock,
    job_service_class: Mock,
    start_import: Mock,
    monitor_class: Mock,
    notification_factory: Mock,
    tmp_path: Path,
) -> None:
    client_class.return_value.application_name = "Vision"
    client_class.return_value.planning_api_root = (
        "https://example.oraclecloud.com/rest/v3"
    )
    start_import.return_value = DataJobSubmission(
        job_id=301,
        job_name="Import Forecast Data",
        file_name="Forecast_Data.csv",
        error_file_name="Forecast_Errors.log",
    )
    monitor_class.return_value.wait_for_completion.return_value = JobResult(
        job_id=301,
        status=0,
        descriptive_status="Completed",
    )
    job_service_class.return_value.get_record_statistics.return_value = (
        JobRecordStatistics(
            records_read=100,
            records_processed=99,
            records_rejected=1,
            details=(
                JobRecordStatisticsItem(
                    records_read=100,
                    records_processed=99,
                    records_rejected=1,
                    load_type="Data Import",
                ),
            ),
        )
    )

    run = OperationCommandExecutor(_settings(tmp_path)).execute(
        DataImportOperationInput(
            job_name="Import Forecast Data",
            inbox_file="Forecast_Data.csv",
            error_file_name="Forecast_Errors.log",
        ),
        execution_id="data-import-run-1",
        log_file=tmp_path / "data-import.log",
    )

    assert run.status is WorkflowStatus.SUCCESS
    assert [step.name for step in run.steps] == [
        "Validate operation inputs",
        "Connect to Oracle EPM",
        "Execute Planning Data Import",
    ]
    start_import.assert_called_once_with(
        "Forecast_Data.csv",
        "Import Forecast Data",
        error_file_name="Forecast_Errors-dataimpo.log",
    )
    execution_details = run.steps[2].details
    assert execution_details["record_statistics"]["records_read"] == 100
    assert (
        execution_details["record_statistics"]["records_processed"] == 99
    )
    assert execution_details["record_statistics"]["records_rejected"] == 1
    job_service_class.return_value.get_record_statistics.assert_called_once_with(
        301
    )
    notification_factory.return_value.publish.assert_called_once()


@patch("app.application.operations.create_notification_service")
@patch("app.application.operations.JobMonitor")
@patch("app.application.operations.DataService.start_import")
@patch("app.application.operations.JobService")
@patch("app.application.operations.EPMClient")
def test_native_data_import_uses_file_configured_in_saved_job(
    client_class: Mock,
    job_service_class: Mock,
    start_import: Mock,
    monitor_class: Mock,
    notification_factory: Mock,
    tmp_path: Path,
) -> None:
    client_class.return_value.application_name = "Vision"
    client_class.return_value.planning_api_root = (
        "https://example.oraclecloud.com/rest/v3"
    )
    start_import.return_value = DataJobSubmission(
        job_id=302,
        job_name="Import Forecast Data",
        file_name=None,
        error_file_name="data-import-errors-configur.zip",
    )
    monitor_class.return_value.wait_for_completion.return_value = JobResult(
        job_id=302,
        status=0,
        descriptive_status="Completed",
    )
    job_service_class.return_value.get_record_statistics.return_value = None

    run = OperationCommandExecutor(_settings(tmp_path)).execute(
        DataImportOperationInput(
            job_name="Import Forecast Data",
            use_configured_file=True,
        ),
        execution_id="configured-data-import-1",
        log_file=tmp_path / "configured-data-import.log",
    )

    assert run.status is WorkflowStatus.SUCCESS
    start_import.assert_called_once_with(
        None,
        "Import Forecast Data",
        error_file_name="data-import-errors-configur.zip",
    )
    monitor_class.return_value.wait_for_completion.assert_called_once_with(302)
    notification_factory.return_value.publish.assert_called_once()


@patch("app.application.operations.create_notification_service")
@patch(
    "app.application.operations."
    "SubstitutionVariableApplicationService.apply"
)
@patch("app.application.operations.EPMClient")
def test_substitution_variable_update_is_governed_and_recorded(
    client_class: Mock,
    apply_change: Mock,
    notification_factory: Mock,
    tmp_path: Path,
) -> None:
    apply_change.return_value = SubstitutionVariableChangeResult(
        action=SubstitutionVariableAction.UPDATE,
        scope="ALL",
        name="CurYr",
        old_value="FY25",
        new_value="FY26",
        changed=True,
    )

    run = OperationCommandExecutor(_settings(tmp_path)).execute(
        SubstitutionVariableOperationInput(
            action=SubstitutionVariableAction.UPDATE,
            scope="ALL",
            name="CurYr",
            value="FY26",
            expected_current_value="FY25",
        ),
        execution_id="variable-run-1",
        log_file=tmp_path / "variable.log",
    )

    assert run.status is WorkflowStatus.SUCCESS
    assert [step.name for step in run.steps] == [
        "Validate operation inputs",
        "Connect to Oracle EPM",
        "Execute Substitution Variable",
    ]
    apply_change.assert_called_once()
    notification_factory.return_value.publish.assert_called_once()


@patch("app.application.operations.create_notification_service")
@patch("app.application.operations.JobMonitor")
@patch("app.application.operations.CubeRefreshService.start_refresh")
@patch("app.application.operations.EPMClient")
def test_standalone_cube_refresh_is_monitored(
    client_class: Mock,
    start_refresh: Mock,
    monitor_class: Mock,
    notification_factory: Mock,
    tmp_path: Path,
) -> None:
    client_class.return_value.application_name = "Vision"
    client_class.return_value.planning_api_root = (
        "https://example.oraclecloud.com/rest/v3"
    )
    start_refresh.return_value = CubeRefreshSubmission(
        job_id=401,
        job_name="RefreshCube",
    )
    monitor_class.return_value.wait_for_completion.return_value = JobResult(
        job_id=401,
        status=0,
        descriptive_status="Completed",
    )

    run = OperationCommandExecutor(_settings(tmp_path)).execute(
        CubeRefreshOperationInput(job_name="RefreshCube"),
        execution_id="cube-refresh-run-1",
        log_file=tmp_path / "cube-refresh.log",
    )

    assert run.status is WorkflowStatus.SUCCESS
    assert [step.name for step in run.steps] == [
        "Validate operation inputs",
        "Connect to Oracle EPM",
        "Execute Planning Cube Refresh",
    ]
    start_refresh.assert_called_once_with("RefreshCube")
    monitor_class.return_value.wait_for_completion.assert_called_once_with(
        401
    )
    notification_factory.return_value.publish.assert_called_once()


@patch("app.application.operations.create_notification_service")
@patch("app.application.operations.ReportWorkspaceService.generate")
@patch("app.application.operations.EPMClient")
def test_standalone_report_generation_creates_an_artifact(
    client_class: Mock,
    generate: Mock,
    notification_factory: Mock,
    tmp_path: Path,
) -> None:
    generate.return_value = ReportGenerationResult(
        form_name="Revenue Report",
        output_path=tmp_path / "Revenue_Report.xlsx",
        row_count=12,
        data_cell_count=144,
        pov=(("Year", "FY26"), ("Scenario", "Forecast")),
    )

    run = OperationCommandExecutor(_settings(tmp_path)).execute(
        ReportGenerationOperationInput(
            form_name="Revenue Report",
            title="Revenue Forecast",
            page_member_overrides=(
                ("Year", "FY26"),
                ("Scenario", "Forecast"),
            ),
        ),
        execution_id="report-run-1",
        log_file=tmp_path / "report.log",
    )

    assert run.status is WorkflowStatus.SUCCESS
    assert [step.name for step in run.steps] == [
        "Validate operation inputs",
        "Connect to Oracle EPM",
        "Execute Report Generation",
    ]
    generate.assert_called_once()
    notification_factory.return_value.publish.assert_called_once()
