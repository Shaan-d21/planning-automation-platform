"""Tests for the command-line entry point."""

from __future__ import annotations

from unittest.mock import Mock, patch

from app.config.email_settings import EmailNotificationSettings
from app.config.settings import DEFAULT_DATA_INTEGRATION_CATALOG_FILE
from app.models.job import JobDefinition
from app.models.notification import TaskNotificationStatus
from app.models.report import DataSliceReportDefinition, ReportAxisSegment
from app.models.pipeline_input import (
    PipelineFileConsumer,
    PipelineFileRequirement,
    PipelineFileSource,
)
from app.utils.exceptions import AuthenticationError, ConfigurationError
from main import (
    _execute_rest_data_integration,
    _normalize_dragged_path,
    _parse_runtime_prompt_arguments,
    _parse_scoped_variable_arguments,
    _prompt_report_name,
    _prompt_report_registration,
    _prompt_pipeline_file_selections,
    _prompt_integration_name,
    _resolve_noninteractive_pipeline_files,
    _select_saved_job_definition,
    _select_business_rule,
    main,
)


def test_report_name_prompt_selects_registered_report() -> None:
    definition = DataSliceReportDefinition(
        name="Revenue Report",
        title="Revenue",
        cube="VisASO",
        pov=(("Scenario", "Actual"),),
        columns=(
            ReportAxisSegment(
                dimensions=("Period",),
                members=(("Jan",),),
            ),
        ),
        rows=(
            ReportAxisSegment(
                dimensions=("Account",),
                members=(("Revenue",),),
            ),
        ),
    )

    selected = _prompt_report_name(
        (definition,),
        input_func=lambda prompt: "1",
        output_func=lambda value: None,
    )

    assert selected == "Revenue Report"


def test_report_registration_prompt_builds_data_slice_definition() -> None:
    responses = iter(
        [
            "",
            "VisASO",
            "Scenario",
            "Actual",
            "",
            "Period, Product",
            "Jan|Feb|Mar",
            "Phone|Laptop",
            "Account",
            "Revenue|Gross Profit",
            "y",
        ]
    )

    definition = _prompt_report_registration(
        "Revenue Form",
        input_func=lambda prompt: next(responses),
        output_func=lambda value: None,
    )

    assert definition is not None
    assert definition.name == "Revenue Form"
    assert definition.title == "Revenue Form Report"
    assert definition.cube == "VisASO"
    assert definition.pov == (("Scenario", "Actual"),)
    assert definition.column_dimensions == ("Period", "Product")
    assert definition.columns[0].members == (
        ("Jan", "Feb", "Mar"),
        ("Phone", "Laptop"),
    )
    assert definition.row_dimensions == ("Account",)


def test_parse_scoped_substitution_variables() -> None:
    parsed = _parse_scoped_variable_arguments(
        ["all.CurYr=FY26", "Plan1.CurMonth=Jan"],
        option_name="--set-subvar",
    )

    assert parsed == {
        ("ALL", "CurYr"): "FY26",
        ("Plan1", "CurMonth"): "Jan",
    }


def test_cube_refresh_selection_accepts_manual_saved_job_name() -> None:
    responses = iter(["2", "Refresh_Cube"])

    selected = _select_saved_job_definition(
        (
            JobDefinition(
                job_name="RefreshCube",
                job_type="Cube Refresh",
            ),
        ),
        title="Available Cube Refresh Jobs",
        default_name=None,
        input_func=lambda prompt: next(responses),
        output_func=lambda value: None,
    )

    assert selected == "Refresh_Cube"


def test_cube_refresh_selection_allows_manual_name_when_discovery_is_empty(
) -> None:
    responses = iter(["1", "RefreshDatabase"])

    selected = _select_saved_job_definition(
        (),
        title="Available Cube Refresh Jobs",
        default_name=None,
        input_func=lambda prompt: next(responses),
        output_func=lambda value: None,
    )

    assert selected == "RefreshDatabase"


@patch("main.ApplicationService")
@patch("main.EPMClient")
@patch("main.Settings.from_env")
def test_main_reports_success(
    from_env: Mock,
    client_class: Mock,
    application_service_class: Mock,
    capsys,
) -> None:
    settings = Mock(log_level="INFO")
    from_env.return_value = settings
    client = client_class.return_value.__enter__.return_value
    application_service_class.return_value.get_configured_application.return_value = (
        Mock(
            name="Vision",
            application_type="PBCS",
            storage="Multidim",
        )
    )

    exit_code = main(["login"])

    assert exit_code == 0
    client.authenticate.assert_called_once_with()
    application_service_class.return_value.get_configured_application.assert_called_once_with()
    assert (
        "Successfully connected to Oracle Planning."
        in capsys.readouterr().out
    )


@patch("main._run_planning_process_command")
@patch("main.Settings.from_env")
def test_noninteractive_process_command_dispatches(
    from_env: Mock,
    run_process: Mock,
) -> None:
    from_env.return_value = Mock(
        log_level="INFO",
        verify_ssl=True,
        email_notifications=EmailNotificationSettings(),
        epm_base_url="https://example.oraclecloud.com",
        application_name="Vision",
    )
    run_process.return_value = 0

    exit_code = main(
        [
            "process",
            "--process",
            "MONTHLY_FORECAST_PROCESS",
            "--year",
            "FY26",
            "--start-period",
            "Jan",
            "--end-period",
            "Mar",
            "--scenario",
            "Forecast",
            "--version",
            "Working",
            "--dry-run",
        ],
        output_func=lambda value: None,
    )

    assert exit_code == 0
    call = run_process.call_args
    assert call.args[0].process_code == "MONTHLY_FORECAST_PROCESS"
    assert call.args[0].year == "FY26"
    assert call.args[0].process_dry_run is True
    assert call.kwargs["interactive"] is False


def test_process_command_requires_cycle_periods(capsys) -> None:
    exit_code = main(
        [
            "process",
            "--process",
            "MONTHLY_FORECAST_PROCESS",
            "--year",
            "FY26",
        ]
    )

    assert exit_code == 1
    assert "requires --start-period and --end-period" in (
        capsys.readouterr().out
    )


@patch("main.create_notification_service")
@patch("main.ApplicationService")
@patch("main.EPMClient")
@patch("main.Settings.from_env")
def test_successful_command_publishes_success_notification(
    from_env: Mock,
    client_class: Mock,
    application_service_class: Mock,
    create_notifications: Mock,
) -> None:
    from_env.return_value = Mock(
        log_level="INFO",
        verify_ssl=True,
        email_notifications=EmailNotificationSettings(),
        epm_base_url="https://example.oraclecloud.com",
        application_name="Plan1",
    )
    application_service_class.return_value.get_configured_application.return_value = (
        Mock(
            name="Plan1",
            application_type="PBCS",
            storage="Multidim",
        )
    )

    exit_code = main(["login"], output_func=lambda value: None)

    assert exit_code == 0
    event = (
        create_notifications.return_value.publish.call_args.args[0]
    )
    assert event.status is TaskNotificationStatus.SUCCESS
    assert event.task_name == "Connection check"


@patch("main.create_notification_service")
@patch("main.EPMClient")
@patch("main.Settings.from_env")
def test_failed_command_publishes_failure_notification(
    from_env: Mock,
    client_class: Mock,
    create_notifications: Mock,
) -> None:
    from app.utils.exceptions import AuthenticationError

    from_env.return_value = Mock(
        log_level="INFO",
        verify_ssl=True,
        email_notifications=EmailNotificationSettings(),
        epm_base_url="https://example.oraclecloud.com",
        application_name="Plan1",
    )
    client_class.return_value.__enter__.return_value.authenticate.side_effect = (
        AuthenticationError("Invalid credentials")
    )

    exit_code = main(["login"], output_func=lambda value: None)

    assert exit_code == 1
    event = (
        create_notifications.return_value.publish.call_args.args[0]
    )
    assert event.status is TaskNotificationStatus.FAILED
    assert event.error_message == "Invalid credentials"


@patch("main._execute_rest_pipeline")
@patch("main.EPMClient")
@patch("main.Settings.from_env")
def test_noninteractive_rest_pipeline(
    from_env: Mock,
    client_class: Mock,
    execute_pipeline: Mock,
) -> None:
    from_env.return_value = Mock(
        log_level="INFO",
        verify_ssl=True,
        email_notifications=EmailNotificationSettings(),
        epm_base_url="https://example.oraclecloud.com",
        application_name="Plan1",
        default_pipeline_engine="rest",
        default_pipeline_code="PIPE01",
    )
    client = client_class.return_value.__enter__.return_value
    client.get.return_value = {
        "status": 0,
        "response": {
            "name": "PIPE01",
            "displayName": "PL_ProductRevenueForecast",
            "variables": [
                {
                    "varName": "STARTPERIOD",
                    "varDisplayName": "Start Period",
                    "varDefaultValue": None,
                    "varSequence": 1,
                },
                {
                    "varName": "ENDPERIOD",
                    "varDisplayName": "End Period",
                    "varDefaultValue": None,
                    "varSequence": 2,
                },
            ],
            "stages": [],
        },
    }

    exit_code = main(
        [
            "pipeline",
            "--pipeline",
            "PIPE01",
            "--variable",
            "STARTPERIOD=Jan-26",
            "--variable",
            "ENDPERIOD=Mar-26",
        ],
        output_func=lambda value: None,
    )

    assert exit_code == 0
    client.authenticate.assert_called_once()
    execute_pipeline.assert_called_once()
    assert execute_pipeline.call_args.kwargs["pipeline_code"] == "PIPE01"
    assert execute_pipeline.call_args.kwargs["variables"] == {
        "STARTPERIOD": "Jan-26",
        "ENDPERIOD": "Mar-26",
    }


def test_pipeline_file_prompt_supports_upload_and_existing_file(
    tmp_path,
) -> None:
    local_file = tmp_path / "new-metadata.csv"
    local_file.write_text("Account,Parent", encoding="utf-8")
    requirements = (
        PipelineFileRequirement(
            key="Metadata.csv",
            display_name="Metadata input",
            variable_name=None,
            configured_reference="Metadata.csv",
            allowed_extensions=frozenset({".csv", ".zip"}),
            consumers=(
                PipelineFileConsumer(
                    stage_name="Metadata",
                    job_name="Import Metadata",
                    job_type="importMetadata",
                    parameter_name="importZipFileName",
                ),
            ),
        ),
        PipelineFileRequirement(
            key="DATA_FILE",
            display_name="Data input",
            variable_name="DATA_FILE",
            configured_reference=None,
            allowed_extensions=frozenset({".csv"}),
            consumers=(),
        ),
    )
    responses = iter(
        [
            "1",
            str(local_file),
            "2",
            "inbox/Data.csv",
        ]
    )

    selections = _prompt_pipeline_file_selections(
        requirements,
        input_func=lambda prompt: next(responses),
        output_func=lambda value: None,
    )

    assert selections is not None
    assert len(selections) == 2
    assert selections[0].source is PipelineFileSource.LOCAL_UPLOAD
    assert selections[0].oracle_reference == "Metadata.csv"
    assert selections[1].source is PipelineFileSource.EXISTING_INBOX
    assert selections[1].oracle_reference == "inbox/Data.csv"


def test_pipeline_local_integration_upload_uses_epm_inbox_reference(
    tmp_path,
) -> None:
    local_file = tmp_path / "latest-data.csv"
    local_file.write_text("Product,Amount\nP1,10", encoding="utf-8")
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
    responses = iter(["1", str(local_file)])

    selections = _prompt_pipeline_file_selections(
        (requirement,),
        input_func=lambda prompt: next(responses),
        output_func=lambda value: None,
    )

    assert selections is not None
    assert selections[0].oracle_reference == (
        "#epminbox/latest-data.csv"
    )


def test_noninteractive_fixed_pipeline_files_default_to_existing() -> None:
    requirement = PipelineFileRequirement(
        key="Metadata.csv",
        display_name="Metadata input",
        variable_name=None,
        configured_reference="Metadata.csv",
        allowed_extensions=frozenset({".csv", ".zip"}),
        consumers=(),
    )

    selections = _resolve_noninteractive_pipeline_files(
        (requirement,),
        supplied_variables={},
        local_uploads={},
        inbox_files={},
    )

    assert len(selections) == 1
    assert selections[0].source is PipelineFileSource.EXISTING_INBOX
    assert selections[0].oracle_reference == "Metadata.csv"


@patch(
    "main.Settings.from_env",
    side_effect=ConfigurationError("Missing required configuration"),
)
def test_main_reports_framework_error(
    from_env: Mock,
    capsys,
) -> None:
    exit_code = main(["login"])

    assert exit_code == 1
    assert "Missing required configuration" in capsys.readouterr().out


@patch("main._execute_metadata_load")
@patch("main.EPMClient")
@patch("main.Settings.from_env")
def test_main_runs_metadata_workflow_when_arguments_are_supplied(
    from_env: Mock,
    client_class: Mock,
    execute_metadata_load: Mock,
    tmp_path,
) -> None:
    metadata_file = tmp_path / "Account.csv"
    metadata_file.write_text("Account,Parent", encoding="utf-8")
    settings = Mock(
        log_level="INFO",
        verify_ssl=True,
        default_metadata_import_mode="job_definition",
        default_metadata_engine="rest",
    )
    from_env.return_value = settings
    client = client_class.return_value.__enter__.return_value
    client.application_name = "Vision"
    client.planning_api_root = "HyperionPlanning/rest/v3"

    exit_code = main(
        [
            "--metadata-file",
            str(metadata_file),
            "--metadata-job-name",
            "Import Account",
        ]
    )

    assert exit_code == 0
    client.authenticate.assert_called_once_with()
    execute_metadata_load.assert_called_once()
    call = execute_metadata_load.call_args
    assert call.kwargs["metadata_file"] == metadata_file
    assert call.kwargs["job_name"] == "Import Account"
    assert call.kwargs["import_mode"] == "job_definition"


def test_main_requires_both_metadata_arguments(capsys) -> None:
    exit_code = main(["--metadata-file", "Account.csv"])

    assert exit_code == 1
    assert (
        "metadata command requires --job"
        in capsys.readouterr().out
    )


@patch("main._execute_metadata_load")
@patch("main.JobService")
@patch("main.EPMClient")
@patch("main.Settings.from_env")
def test_interactive_menu_selects_file_and_discovered_job(
    from_env: Mock,
    client_class: Mock,
    job_service_class: Mock,
    execute_metadata_load: Mock,
    tmp_path,
) -> None:
    metadata_file = tmp_path / "Account Metadata.csv"
    settings = Mock(
        log_level="INFO",
        verify_ssl=True,
        default_metadata_import_mode="job_definition",
        default_metadata_engine="rest",
    )
    from_env.return_value = settings
    job_service = job_service_class.return_value
    job_service.get_job_definitions.return_value = (
        JobDefinition(
            job_name="Import Account",
            job_type="IMPORT_METADATA",
        ),
    )
    responses = iter(
        [
            "2",
            "1",
            "1",
            f'"{metadata_file}"',
            "1",
                "",
                "y",
                "n",
            ]
        )
    output: list[str] = []

    exit_code = main(
        input_func=lambda prompt: next(responses),
        output_func=output.append,
    )

    assert exit_code == 0
    client_class.return_value.__enter__.return_value.authenticate.assert_called_once()
    job_service.get_job_definitions.assert_called_once_with(
        job_type="IMPORT_METADATA"
    )
    call = execute_metadata_load.call_args
    assert call.kwargs["metadata_file"] == metadata_file
    assert call.kwargs["job_name"] == "Import Account"


@patch("main._execute_epm_automate_metadata_load")
@patch("main.EPMClient")
@patch("main.Settings.from_env")
def test_noninteractive_epm_automate_skips_rest_client(
    from_env: Mock,
    client_class: Mock,
    execute_epm_automate: Mock,
    tmp_path,
) -> None:
    metadata_file = tmp_path / "Metadata.zip"
    metadata_file.write_bytes(b"content")
    from_env.return_value = Mock(
        log_level="INFO",
        verify_ssl=True,
        default_metadata_engine="rest",
    )

    exit_code = main(
        [
            "metadata",
            "--engine",
            "epmautomate",
            "--file",
            str(metadata_file),
            "--job",
            "Import Metadata",
        ]
    )

    assert exit_code == 0
    client_class.assert_not_called()
    execute_epm_automate.assert_called_once()
    assert (
        execute_epm_automate.call_args.kwargs["job_name"]
        == "Import Metadata"
    )


@patch("main._execute_data_load")
@patch("main.EPMClient")
@patch("main.Settings.from_env")
def test_noninteractive_rest_data_load(
    from_env: Mock,
    client_class: Mock,
    execute_data_load: Mock,
    tmp_path,
) -> None:
    data_file = tmp_path / "PlanData.txt"
    data_file.write_bytes(b"content")
    settings = Mock(
        log_level="INFO",
        verify_ssl=True,
        default_data_engine="rest",
    )
    from_env.return_value = settings
    client = client_class.return_value.__enter__.return_value
    client.application_name = "Vision"
    client.planning_api_root = "HyperionPlanning/rest/v3"

    exit_code = main(
        [
            "data",
            "--engine",
            "rest",
            "--file",
            str(data_file),
            "--job",
            "Import Plan Data",
        ]
    )

    assert exit_code == 0
    client_class.return_value.__enter__.return_value.authenticate.assert_called_once()
    call_details = execute_data_load.call_args
    assert call_details.kwargs["data_file"] == data_file
    assert call_details.kwargs["job_name"] == "Import Plan Data"


@patch("main._execute_epm_automate_data_load")
@patch("main.EPMClient")
@patch("main.Settings.from_env")
def test_noninteractive_epm_automate_data_skips_rest_client(
    from_env: Mock,
    client_class: Mock,
    execute_data_load: Mock,
    tmp_path,
) -> None:
    data_file = tmp_path / "PlanData.zip"
    data_file.write_bytes(b"content")
    from_env.return_value = Mock(
        log_level="INFO",
        verify_ssl=True,
        default_data_engine="rest",
    )

    exit_code = main(
        [
            "data",
            "--engine",
            "epmautomate",
            "--file",
            str(data_file),
            "--job",
            "Import Plan Data",
        ]
    )

    assert exit_code == 0
    client_class.assert_not_called()
    execute_data_load.assert_called_once()


@patch("main._execute_data_load")
@patch("main.JobService")
@patch("main.EPMClient")
@patch("main.Settings.from_env")
def test_interactive_menu_selects_data_job(
    from_env: Mock,
    client_class: Mock,
    job_service_class: Mock,
    execute_data_load: Mock,
    tmp_path,
) -> None:
    data_file = tmp_path / "PlanData.csv"
    from_env.return_value = Mock(
        log_level="INFO",
        verify_ssl=True,
        default_data_engine="rest",
    )
    job_service = job_service_class.return_value
    job_service.get_job_definitions.return_value = (
        JobDefinition(
            job_name="Import Plan Data",
            job_type="IMPORT_DATA",
        ),
    )
    responses = iter(
        [
            "3",
            "1",
            "1",
            str(data_file),
            "1",
            "",
            "y",
        ]
    )

    exit_code = main(
        input_func=lambda prompt: next(responses),
        output_func=lambda value: None,
    )

    assert exit_code == 0
    job_service.get_job_definitions.assert_called_once_with(
        job_type="IMPORT_DATA"
    )
    assert execute_data_load.call_args.kwargs["data_file"] == data_file


def test_data_command_rejects_metadata_import_mode(capsys) -> None:
    exit_code = main(
        [
            "data",
            "--file",
            "PlanData.csv",
            "--job",
            "Import Plan Data",
            "--import-mode",
            "job_definition",
        ]
    )

    assert exit_code == 1
    assert "metadata and integration" in capsys.readouterr().out


@patch("main._execute_epm_automate_data_integration")
@patch("main.Settings.from_env")
def test_noninteractive_epm_automate_data_integration(
    from_env: Mock,
    execute_integration: Mock,
    tmp_path,
) -> None:
    data_file = tmp_path / "Test_Sales_DataLoad_V2.csv"
    data_file.write_text(
        "Total Sales,BaseData,500,Working,Galaxy S24,100,200,300",
        encoding="utf-8",
    )
    from_env.return_value = Mock(
        log_level="INFO",
        verify_ssl=True,
        default_data_integration_name="Test_DataLoad",
        default_data_integration_import_mode="Replace",
        default_data_integration_export_mode="Merge",
        default_data_integration_period_count=3,
        default_data_integration_first_data_column=6,
        default_data_integration_engine="epmautomate",
        data_integration_catalog_file=(
            DEFAULT_DATA_INTEGRATION_CATALOG_FILE
        ),
    )

    exit_code = main(
        [
            "integration",
            "--engine",
            "epmautomate",
            "--file",
            str(data_file),
            "--start-period",
            "Jun-19",
            "--end-period",
            "Aug-19",
        ]
    )

    assert exit_code == 0
    call_details = execute_integration.call_args.kwargs
    assert call_details["integration_name"] == "Test_DataLoad"
    assert (
        call_details["period_range"].oracle_period_name
        == "{Jun-19}{Aug-19}"
    )


@patch("main._execute_epm_automate_data_integration")
@patch("main.Settings.from_env")
def test_interactive_data_integration_selects_year_and_periods(
    from_env: Mock,
    execute_integration: Mock,
    tmp_path,
) -> None:
    data_file = tmp_path / "Test_Sales_DataLoad_V2.csv"
    data_file.write_text(
        "Total Sales,BaseData,500,Working,Galaxy S24,100,200,300",
        encoding="utf-8",
    )
    from_env.return_value = Mock(
        log_level="INFO",
        verify_ssl=True,
        default_data_integration_name="Test_DataLoad",
        default_data_integration_import_mode="Replace",
        default_data_integration_export_mode="Merge",
        default_data_integration_period_count=3,
        default_data_integration_first_data_column=6,
        default_data_integration_engine="epmautomate",
        data_integration_catalog_file=(
            DEFAULT_DATA_INTEGRATION_CATALOG_FILE
        ),
    )
    responses = iter(
        [
            "4",
            "2",
            "1",
            str(data_file),
            "",
            "Jun-19",
            "Aug-19",
            "y",
        ]
    )
    output: list[str] = []

    exit_code = main(
        input_func=lambda prompt: next(responses),
        output_func=output.append,
    )

    assert exit_code == 0
    period_range = execute_integration.call_args.kwargs["period_range"]
    assert period_range.periods == ("Jun-19", "Jul-19", "Aug-19")
    summary = "\n".join(output)
    assert "Period parameter: {Jun-19}{Aug-19}" in summary
    assert "controlled by the Oracle Data Integration definition" in summary


def test_integration_selector_returns_catalog_choice(tmp_path) -> None:
    catalog = tmp_path / "integrations.json"
    catalog.write_text(
        """
        {
          "integrations": [
            {"name": "Revenue_Load", "description": "Revenue"},
            {"name": "Headcount_Load", "description": "Headcount"}
          ]
        }
        """,
        encoding="utf-8",
    )
    responses = iter(["2"])
    output: list[str] = []

    selected = _prompt_integration_name(
        default_name="Revenue_Load",
        catalog_file=catalog,
        input_func=lambda prompt: next(responses),
        output_func=output.append,
    )

    assert selected == "Headcount_Load"
    menu = "\n".join(output)
    assert "1. Revenue_Load - Revenue" in menu
    assert "2. Headcount_Load - Headcount" in menu
    assert "3. Enter another integration name" in menu


def test_integration_selector_supports_manual_name(tmp_path) -> None:
    catalog = tmp_path / "integrations.json"
    catalog.write_text(
        '{"integrations": [{"name": "Revenue_Load"}]}',
        encoding="utf-8",
    )
    responses = iter(["2", "New_Integration"])

    selected = _prompt_integration_name(
        default_name=None,
        catalog_file=catalog,
        input_func=lambda prompt: next(responses),
        output_func=lambda value: None,
    )

    assert selected == "New_Integration"


def test_integration_command_rejects_incomplete_period_range(capsys) -> None:
    exit_code = main(
        [
            "integration",
            "--inbox-file",
            "Test.csv",
            "--start-period",
            "Jun-19",
        ]
    )

    assert exit_code == 1
    assert "both --start-period" in capsys.readouterr().out


@patch("main._execute_epm_automate_data_integration")
@patch("main.Settings.from_env")
def test_integration_does_not_inspect_file_layout_or_period_count(
    from_env: Mock,
    execute_integration: Mock,
    tmp_path,
) -> None:
    source = tmp_path / "VariableLayout.dat"
    source.write_text("Dim1|Dim2|Amount", encoding="utf-8")
    from_env.return_value = Mock(
        log_level="INFO",
        verify_ssl=True,
        default_data_integration_name="Delimited_Data_Load",
        default_data_integration_import_mode="Replace",
        default_data_integration_export_mode="Merge",
        default_data_integration_engine="epmautomate",
    )

    exit_code = main(
        [
            "integration",
            "--engine",
            "epmautomate",
            "--file",
            str(source),
            "--start-period",
            "Jun-19",
            "--end-period",
            "Jun-19",
        ]
    )

    assert exit_code == 0
    period_range = execute_integration.call_args.kwargs["period_range"]
    assert period_range.oracle_period_name == "{Jun-19}"


@patch("main.JobMonitor")
@patch("main.DataIntegrationService")
@patch("main.FileService")
def test_rest_local_upload_is_referenced_from_epminbox(
    file_service_class: Mock,
    integration_service_class: Mock,
    monitor_class: Mock,
    tmp_path,
) -> None:
    source = tmp_path / "Test_Product_Revenue_DataLoad.csv"
    source.write_text("Product,Revenue", encoding="utf-8")
    file_service_class.return_value.upload_to_inbox.return_value = Mock(
        file_name=source.name
    )
    integration_service_class.return_value.start_integration.return_value = (
        Mock(
            job_id=164,
            integration_name="Test_Load_Product_Revenue",
            period_name="{Apr-26}",
        )
    )
    monitor_class.return_value.wait_for_completion.return_value = Mock(
        job_id=164
    )

    _execute_rest_data_integration(
        Mock(),
        Mock(default_poll_interval=1, default_job_timeout=30),
        data_file=source,
        inbox_file_name=None,
        integration_name="Test_Load_Product_Revenue",
        period_range=Mock(oracle_period_name="{Apr-26}"),
        import_mode="Replace",
        export_mode="Merge",
        logger=Mock(),
        output_func=lambda value: None,
    )

    start_call = (
        integration_service_class.return_value.start_integration.call_args
    )
    assert start_call.args[0] == (
        "#epminbox/Test_Product_Revenue_DataLoad.csv"
    )


def test_normalize_dragged_path_removes_matching_quotes() -> None:
    assert _normalize_dragged_path(' "C:\\metadata\\Account.csv" ') == (
        "C:\\metadata\\Account.csv"
    )


@patch("main._execute_rest_business_rule")
@patch("main.EPMClient")
@patch("main.Settings.from_env")
def test_noninteractive_rest_business_rule_with_runtime_prompts(
    from_env: Mock,
    client_class: Mock,
    execute_rule: Mock,
) -> None:
    from_env.return_value = Mock(
        log_level="INFO",
        verify_ssl=True,
        default_business_rule_engine="rest",
    )
    client = client_class.return_value.__enter__.return_value
    client.application_name = "Vision"
    client.planning_api_root = "HyperionPlanning/rest/v3"

    exit_code = main(
        [
            "rule",
            "--engine",
            "rest",
            "--rule",
            "Calculate Vehicle Revenue",
            "--rtp",
            "Scenario=Plan",
            "--rtp",
            "Entity=North America",
        ],
        output_func=lambda value: None,
    )

    assert exit_code == 0
    client_class.return_value.__enter__.return_value.authenticate.assert_called_once()
    call_details = execute_rule.call_args.kwargs
    assert call_details["rule_name"] == "Calculate Vehicle Revenue"
    assert call_details["runtime_prompts"] == {
        "Scenario": "Plan",
        "Entity": "North America",
    }


@patch("main._execute_epm_automate_business_rule")
@patch("main.EPMClient")
@patch("main.Settings.from_env")
def test_noninteractive_epm_automate_business_rule_skips_rest(
    from_env: Mock,
    client_class: Mock,
    execute_rule: Mock,
) -> None:
    from_env.return_value = Mock(
        log_level="INFO",
        verify_ssl=True,
        default_business_rule_engine="rest",
    )

    exit_code = main(
        [
            "rule",
            "--engine",
            "epmautomate",
            "--rule",
            "Calculate Revenue",
        ],
        output_func=lambda value: None,
    )

    assert exit_code == 0
    client_class.assert_not_called()
    assert execute_rule.call_args.kwargs["rule_name"] == "Calculate Revenue"
    assert execute_rule.call_args.kwargs["runtime_prompts"] == {}


@patch("main._execute_epm_automate_business_rule")
@patch("main.JobService")
@patch("main.EPMClient")
@patch("main.Settings.from_env")
def test_interactive_business_rule_selection_and_runtime_prompts(
    from_env: Mock,
    client_class: Mock,
    job_service_class: Mock,
    execute_rule: Mock,
) -> None:
    from_env.return_value = Mock(
        log_level="INFO",
        verify_ssl=True,
        default_business_rule_engine="rest",
    )
    job_service_class.return_value.get_job_definitions.return_value = (
        JobDefinition(
            job_name="Calculate Vehicle Revenue",
            job_type="RULES",
        ),
    )
    responses = iter(
        [
            "5",
            "2",
            "1",
            "Scenario=Plan",
            "Entity=USA",
            "",
            "y",
        ]
    )
    output: list[str] = []

    exit_code = main(
        input_func=lambda prompt: next(responses),
        output_func=output.append,
    )

    assert exit_code == 0
    job_service_class.return_value.get_job_definitions.assert_called_once_with(
        job_type="RULES"
    )
    call_details = execute_rule.call_args.kwargs
    assert call_details["rule_name"] == "Calculate Vehicle Revenue"
    assert call_details["runtime_prompts"] == {
        "Scenario": "Plan",
        "Entity": "USA",
    }
    assert "Available Business Rules" in "\n".join(output)


def test_rule_command_rejects_invalid_runtime_prompt(capsys) -> None:
    exit_code = main(
        [
            "rule",
            "--rule",
            "Calculate Revenue",
            "--rtp",
            "Entity",
        ]
    )

    assert exit_code == 1
    assert "must use NAME=VALUE" in capsys.readouterr().out


def test_runtime_prompt_parser_preserves_equals_in_value() -> None:
    prompts = _parse_runtime_prompt_arguments(
        ["Expression=Account=Revenue"]
    )

    assert prompts == {"Expression": "Account=Revenue"}


def test_business_rule_selection_falls_back_on_forbidden_discovery() -> None:
    job_service = Mock()
    job_service.get_job_definitions.side_effect = AuthenticationError(
        "Forbidden",
        status_code=403,
    )
    responses = iter(["1", "Manually Entered Rule"])
    output: list[str] = []

    selected = _select_business_rule(
        job_service,
        input_func=lambda prompt: next(responses),
        output_func=output.append,
    )

    assert selected == "Manually Entered Rule"
    assert "cannot list Planning job definitions" in "\n".join(output)
