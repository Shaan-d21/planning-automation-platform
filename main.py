"""Command-line and interactive entry point for Oracle EPM automation."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path, PurePath

from app.automation.epm_automate_runner import EPMAutomateRunner
from app.clients.epm_client import EPMClient
from app.config.email_settings import EmailNotificationSettings
from app.config.settings import PROJECT_ROOT, Settings
from app.infrastructure.database.migration import assert_schema_current
from app.models.business_rule import BusinessRuleSubmission
from app.models.data_integration import (
    DataIntegrationFileReference,
    DataIntegrationPeriodRange,
)
from app.models.data_integration_catalog import DataIntegrationDefinition
from app.models.data_map import DataMapSubmission
from app.models.data_validation import FormLayout
from app.models.job import JobDefinition, JobRecordStatistics, JobResult
from app.models.planning_cycle import (
    CycleValueRole,
    CycleVariableBinding,
    PlanningCycle,
    PlanningCycleDefinition,
)
from app.models.planning_process import (
    PlanningProcessDefinition,
    PlanningProcessStepDefinition,
    PlanningProcessStepType,
)
from app.models.notification import (
    TaskNotificationEvent,
    TaskNotificationStatus,
)
from app.models.pipeline import PipelineDetails, PipelineSubmission
from app.models.pipeline_catalog import PipelineCatalogDefinition
from app.models.pipeline_input import (
    PipelineFileRequirement,
    PipelineFileSelection,
    PipelineFileSource,
)
from app.models.report import (
    DataSliceReportDefinition,
    FormReportRequest,
    ReportAxisSegment,
)
from app.models.substitution_variable import (
    PlanType,
    SubstitutionVariable,
    SubstitutionVariableUpdate,
)
from app.models.workflow import WorkflowRun
from app.monitoring.job_monitor import JobMonitor
from app.services.business_rule_service import BusinessRuleService
from app.services.application_service import ApplicationService
from app.services.data_integration_service import DataIntegrationService
from app.services.data_integration_catalog_service import (
    DataIntegrationCatalogService,
)
from app.services.data_service import DataService
from app.services.data_map_service import DataMapService
from app.services.data_validation_service import DataValidationService
from app.services.cube_refresh_service import CubeRefreshService
from app.services.epm_automate_data_integration_service import (
    EPMAutomateDataIntegrationService,
)
from app.services.epm_automate_data_service import EPMAutomateDataService
from app.services.epm_automate_data_map_service import (
    EPMAutomateDataMapService,
)
from app.services.epm_automate_business_rule_service import (
    EPMAutomateBusinessRuleService,
)
from app.services.epm_automate_metadata_service import (
    EPMAutomateMetadataService,
)
from app.services.epm_automate_pipeline_service import (
    EPMAutomatePipelineService,
)
from app.services.file_service import FileService
from app.services.job_service import JobService
from app.services.metadata_service import MetadataService
from app.services.notification_service import create_notification_service
from app.services.pipeline_catalog_service import PipelineCatalogService
from app.services.pipeline_preflight_service import (
    PipelinePreflightService,
    build_pipeline_file_selection,
    local_upload_oracle_reference,
)
from app.services.pipeline_service import PipelineService
from app.services.planning_cycle_catalog_service import (
    PlanningCycleCatalogService,
)
from app.services.planning_cycle_preflight_service import (
    PlanningCyclePreflightService,
)
from app.services.planning_process_catalog_service import (
    PlanningProcessCatalogService,
)
from app.services.planning_process_orchestrator import (
    PlanningProcessOrchestrator,
)
from app.services.report_service import FormReportService
from app.services.substitution_variable_service import (
    SubstitutionVariableService,
)
from app.services.workflow_engine import WorkflowEngine, WorkflowStep
from app.services.workflow_repository import SQLWorkflowRepository
from app.utils.exceptions import (
    AuthenticationError,
    ConfigurationError,
    DataIntegrationError,
    DataValidationError,
    EPMError,
    JobFailedError,
    ReportGenerationError,
)
from app.utils.logger import configure_logging

InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], None]


def main(
    argv: Sequence[str] = (),
    *,
    input_func: InputFunction = input,
    output_func: OutputFunction = print,
) -> int:
    """Run an interactive menu or a non-interactive automation command."""
    arguments = _parse_arguments(argv)
    logger = configure_logging()
    logger.info("Application started.")

    try:
        command = _resolve_command(arguments)
        if command is None:
            return _run_interactive_menu(
                input_func=input_func,
                output_func=output_func,
            )

        _validate_command_arguments(command, arguments)
        return _run_command(
            command,
            arguments,
            interactive=False,
            input_func=input_func,
            output_func=output_func,
        )
    except JobFailedError as exc:
        _display_job_failure(exc, output_func)
        logging.getLogger("oracle_planning_automation").error(
            "Planning job failed: %s",
            exc,
        )
        return 1
    except EPMError as exc:
        logging.getLogger("oracle_planning_automation").error(
            "Application failed: %s",
            exc,
        )
        output_func(f"Oracle Planning automation failed: {exc}")
        return 1


def _parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Oracle EPM Planning automation. Run without arguments for the "
            "interactive menu."
        )
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=(
            "login",
            "metadata",
            "data",
            "integration",
            "rule",
            "pipeline",
            "data-map",
            "workflow",
            "cycle",
            "history",
            "refresh-cube",
            "variables",
            "report",
            "process",
        ),
        help="Non-interactive command to execute.",
    )
    parser.add_argument(
        "--file",
        "--metadata-file",
        "--data-file",
        dest="metadata_file",
        metavar="FILE",
        type=Path,
        help="Local metadata or data file to upload.",
    )
    parser.add_argument(
        "--inbox-file",
        help="Input filename already in the Oracle Inbox.",
    )
    parser.add_argument(
        "--job",
        "--metadata-job-name",
        "--data-job-name",
        "--integration-name",
        dest="metadata_job_name",
        metavar="JOB",
        help="Name of the saved Planning job or Data Integration.",
    )
    parser.add_argument(
        "--rule",
        "--rule-name",
        dest="rule_name",
        metavar="RULE",
        help="Exact name of a deployed Oracle Planning Business Rule.",
    )
    parser.add_argument(
        "--rtp",
        dest="runtime_prompts",
        metavar="NAME=VALUE",
        action="append",
        default=[],
        help=(
            "Business Rule runtime prompt. Repeat this option for each "
            "prompt."
        ),
    )
    parser.add_argument(
        "--pipeline",
        "--pipeline-code",
        dest="pipeline_code",
        metavar="CODE",
        help="Immutable code of an Oracle Data Integration Pipeline.",
    )
    parser.add_argument(
        "--data-map",
        dest="data_map_name",
        metavar="NAME",
        help="Name of an existing Oracle Planning Data Map.",
    )
    parser.add_argument(
        "--map-override",
        dest="data_map_overrides",
        metavar="DIMENSION=SELECTION",
        action="append",
        default=[],
        help="Override a Data Map member selection. Repeat as needed.",
    )
    parser.add_argument(
        "--map-exclude",
        dest="data_map_exclusions",
        metavar="DIMENSION=SELECTION",
        action="append",
        default=[],
        help="Exclude members from a Data Map selection. Repeat as needed.",
    )
    parser.add_argument(
        "--clear-target",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Clear the Data Map target region before copying data.",
    )
    parser.add_argument(
        "--skip-data-map",
        action="store_true",
        help="Run the workflow without publishing through a Data Map.",
    )
    parser.add_argument(
        "--source-form",
        help="Source validation form with the Data Map source slice.",
    )
    parser.add_argument(
        "--target-form",
        help="Target validation form with the same grid layout.",
    )
    parser.add_argument(
        "--validation-tolerance",
        type=float,
        help="Allowed absolute difference for source-target validation.",
    )
    parser.add_argument(
        "--report-form",
        help="Exact Planning form name used to generate an Excel report.",
    )
    parser.add_argument(
        "--report-output",
        type=Path,
        help="Local .xlsx output path. Defaults to REPORT_OUTPUT_DIR.",
    )
    parser.add_argument(
        "--report-title",
        help="Optional report title displayed in the generated workbook.",
    )
    parser.add_argument(
        "--page-member",
        dest="report_page_members",
        metavar="DIMENSION=MEMBER",
        action="append",
        default=[],
        help="Planning form page POV override. Repeat as needed.",
    )
    parser.add_argument(
        "--filter-member",
        dest="report_filter_members",
        metavar="MEMBER",
        action="append",
        default=[],
        help="Additional Planning form member filter. Repeat as needed.",
    )
    parser.add_argument(
        "--overwrite-report",
        action="store_true",
        help="Allow an existing report output file to be replaced.",
    )
    parser.add_argument(
        "--cycle",
        dest="cycle_code",
        metavar="CODE",
        help="Configured Planning cycle code.",
    )
    parser.add_argument(
        "--process",
        dest="process_code",
        metavar="CODE",
        help="Configured end-to-end Planning process code.",
    )
    parser.add_argument(
        "--run-refresh-cube",
        dest="process_run_refresh",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable the process Cube Refresh step.",
    )
    parser.add_argument(
        "--skip-report",
        action="store_true",
        help="Run the Planning process without its report step.",
    )
    parser.add_argument(
        "--dry-run",
        dest="process_dry_run",
        action="store_true",
        help="Validate a Planning process without executing any steps.",
    )
    parser.add_argument(
        "--year",
        help="Planning cycle year, for example FY26.",
    )
    parser.add_argument(
        "--scenario",
        help="Optional Planning cycle scenario.",
    )
    parser.add_argument(
        "--version",
        dest="cycle_version",
        help="Optional Planning cycle version.",
    )
    parser.add_argument(
        "--configure-cycle",
        action="store_true",
        help="Reconfigure substitution-variable mappings interactively.",
    )
    parser.add_argument(
        "--execution-id",
        help="Workflow execution ID to display from local history.",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=10,
        help="Maximum recent workflow runs to display.",
    )
    parser.add_argument(
        "--refresh-job",
        help="Exact saved CUBE_REFRESH job name.",
    )
    parser.add_argument(
        "--refresh-after-metadata",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Run a saved Cube Refresh job after metadata succeeds.",
    )
    parser.add_argument(
        "--set-subvar",
        dest="substitution_variable_updates",
        metavar="SCOPE.NAME=VALUE",
        action="append",
        default=[],
        help="Update an existing scoped substitution variable.",
    )
    parser.add_argument(
        "--create-subvar",
        dest="substitution_variable_creations",
        metavar="SCOPE.NAME=VALUE",
        action="append",
        default=[],
        help="Explicitly create a scoped substitution variable.",
    )
    parser.add_argument(
        "--variable",
        "--param",
        dest="pipeline_variables",
        metavar="NAME=VALUE",
        action="append",
        default=[],
        help=(
            "Pipeline runtime variable. Repeat this option for each "
            "variable."
        ),
    )
    parser.add_argument(
        "--pipeline-upload",
        dest="pipeline_uploads",
        metavar="INPUT=LOCAL_FILE",
        action="append",
        default=[],
        help=(
            "Upload a local file for a discovered pipeline input. Repeat "
            "for multiple inputs."
        ),
    )
    parser.add_argument(
        "--pipeline-inbox",
        dest="pipeline_inbox_files",
        metavar="INPUT=ORACLE_FILE",
        action="append",
        default=[],
        help=(
            "Use an existing Oracle Inbox file for a discovered pipeline "
            "input. Repeat for multiple inputs."
        ),
    )
    parser.add_argument(
        "--import-mode",
        help=(
            "Metadata import mode or Data Integration import mode. Metadata "
            "supports job_definition."
        ),
    )
    parser.add_argument(
        "--export-mode",
        help="Data Integration export mode, for example Merge.",
    )
    parser.add_argument(
        "--start-period",
        help=(
            "First Oracle Data Integration period, e.g. Jun-19 or Jun#FY19."
        ),
    )
    parser.add_argument(
        "--end-period",
        help=(
            "Last Oracle Data Integration period, e.g. Aug-19 or Aug#FY19."
        ),
    )
    parser.add_argument(
        "--error-file-name",
        help=(
            "Optional ZIP filename for rejected import records in the "
            "Oracle EPM Outbox."
        ),
    )
    parser.add_argument(
        "--engine",
        dest="metadata_engine",
        choices=("rest", "epmautomate"),
        help=(
            "Execution engine. Defaults to the operation-specific setting."
        ),
    )
    return parser.parse_args(list(argv))


def _resolve_command(arguments: argparse.Namespace) -> str | None:
    if arguments.command:
        return str(arguments.command)
    if getattr(arguments, "process_code", None):
        return "process"
    if (
        getattr(arguments, "report_form", None)
        or getattr(arguments, "report_output", None)
        or getattr(arguments, "report_title", None)
        or getattr(arguments, "report_page_members", ())
        or getattr(arguments, "report_filter_members", ())
        or getattr(arguments, "overwrite_report", False)
    ):
        return "report"
    if getattr(arguments, "cycle_code", None):
        return "cycle"
    if getattr(arguments, "execution_id", None):
        return "history"
    if (
        getattr(arguments, "substitution_variable_updates", ())
        or getattr(arguments, "substitution_variable_creations", ())
    ):
        return "variables"
    if (
        getattr(arguments, "data_map_name", None)
        or getattr(arguments, "data_map_overrides", ())
        or getattr(arguments, "data_map_exclusions", ())
        or getattr(arguments, "clear_target", None) is not None
    ):
        return "data-map"
    if (
        arguments.pipeline_code
        or arguments.pipeline_variables
        or arguments.pipeline_uploads
        or arguments.pipeline_inbox_files
    ):
        return "pipeline"
    if arguments.rule_name or arguments.runtime_prompts:
        return "rule"
    if arguments.start_period or arguments.end_period or arguments.export_mode:
        return "integration"
    if _has_import_arguments(arguments):
        return "metadata"
    if getattr(arguments, "refresh_job", None):
        return "refresh-cube"
    return None


def _has_import_arguments(arguments: argparse.Namespace) -> bool:
    return any(
        (
            arguments.metadata_file is not None,
            bool(arguments.inbox_file),
            bool(arguments.metadata_job_name),
            bool(arguments.import_mode),
            bool(arguments.error_file_name),
            bool(arguments.metadata_engine),
            bool(arguments.export_mode),
            bool(arguments.start_period),
            bool(arguments.end_period),
        )
    )


def _validate_command_arguments(
    command: str,
    arguments: argparse.Namespace,
) -> None:
    source_form = getattr(arguments, "source_form", None)
    target_form = getattr(arguments, "target_form", None)
    tolerance = getattr(arguments, "validation_tolerance", None)
    if bool(source_form) != bool(target_form):
        raise ConfigurationError(
            "--source-form and --target-form must be supplied together."
        )
    if tolerance is not None and tolerance < 0:
        raise ConfigurationError(
            "--validation-tolerance cannot be negative."
        )

    process_options_used = any(
        (
            bool(getattr(arguments, "process_code", None)),
            getattr(arguments, "process_run_refresh", None) is not None,
            bool(getattr(arguments, "skip_report", False)),
            bool(getattr(arguments, "process_dry_run", False)),
        )
    )
    if command != "process" and process_options_used:
        raise ConfigurationError(
            "Process options are only supported by the process command."
        )

    report_arguments_used = any(
        (
            bool(getattr(arguments, "report_form", None)),
            getattr(arguments, "report_output", None) is not None,
            bool(getattr(arguments, "report_title", None)),
            bool(getattr(arguments, "report_page_members", ())),
            bool(getattr(arguments, "report_filter_members", ())),
            bool(getattr(arguments, "overwrite_report", False)),
        )
    )
    if command != "report" and report_arguments_used:
        raise ConfigurationError(
            "Report options are only supported by the report command."
        )

    if command == "report":
        unsupported = (
            arguments.metadata_file is not None,
            bool(arguments.inbox_file),
            bool(arguments.metadata_job_name),
            bool(arguments.rule_name),
            bool(arguments.runtime_prompts),
            bool(arguments.pipeline_code),
            bool(arguments.pipeline_variables),
            bool(arguments.pipeline_uploads),
            bool(arguments.pipeline_inbox_files),
            bool(getattr(arguments, "data_map_name", None)),
            bool(getattr(arguments, "cycle_code", None)),
            bool(getattr(arguments, "refresh_job", None)),
            bool(arguments.start_period),
            bool(arguments.end_period),
            bool(arguments.import_mode),
            bool(arguments.export_mode),
            bool(arguments.error_file_name),
            bool(source_form),
            bool(target_form),
        )
        if any(unsupported):
            raise ConfigurationError(
                "The report command supports --report-form, "
                "--report-output, --report-title, --page-member, "
                "--filter-member, --overwrite-report, and --engine rest."
            )
        if not getattr(arguments, "report_form", None):
            raise ConfigurationError(
                "The report command requires --report-form."
            )
        if (
            arguments.metadata_engine
            and arguments.metadata_engine != "rest"
        ):
            raise ConfigurationError(
                "Planning form reports use the REST engine."
            )
        if (
            arguments.report_output is not None
            and arguments.report_output.suffix.lower() != ".xlsx"
        ):
            raise ConfigurationError(
                "--report-output must use the .xlsx extension."
            )
        _parse_name_value_arguments(
            arguments.report_page_members,
            option_name="--page-member",
        )
        return

    if command == "process":
        unsupported = (
            arguments.metadata_file is not None,
            bool(arguments.inbox_file),
            bool(arguments.metadata_job_name),
            bool(arguments.rule_name),
            bool(arguments.runtime_prompts),
            bool(arguments.import_mode),
            bool(arguments.export_mode),
            bool(arguments.error_file_name),
            bool(getattr(arguments, "execution_id", None)),
            bool(getattr(arguments, "refresh_job", None)),
            getattr(arguments, "refresh_after_metadata", None) is not None,
            bool(getattr(arguments, "cycle_code", None)),
        )
        if any(unsupported):
            raise ConfigurationError(
                "The process command supports process and cycle values, "
                "Pipeline inputs, Data Map options, validation, optional "
                "refresh/report controls, and --engine rest only."
            )
        if (
            arguments.metadata_engine
            and arguments.metadata_engine != "rest"
        ):
            raise ConfigurationError(
                "Planning Process orchestration uses the REST engine."
            )
        if not getattr(arguments, "year", None):
            raise ConfigurationError(
                "The process command requires --year."
            )
        if not arguments.start_period or not arguments.end_period:
            raise ConfigurationError(
                "The process command requires --start-period and "
                "--end-period."
            )
        if getattr(arguments, "configure_cycle", False):
            raise ConfigurationError(
                "--configure-cycle is interactive only."
            )
        _parse_pipeline_variable_arguments(arguments.pipeline_variables)
        _parse_named_pipeline_files(
            arguments.pipeline_uploads,
            option_name="--pipeline-upload",
        )
        _parse_named_pipeline_files(
            arguments.pipeline_inbox_files,
            option_name="--pipeline-inbox",
        )
        _parse_name_value_arguments(
            getattr(arguments, "data_map_overrides", ()),
            option_name="--map-override",
        )
        _parse_name_value_arguments(
            getattr(arguments, "data_map_exclusions", ()),
            option_name="--map-exclude",
        )
        return

    if command == "variables":
        if (
            arguments.metadata_engine
            and arguments.metadata_engine != "rest"
        ):
            raise ConfigurationError(
                "Substitution Variable maintenance uses REST only."
            )
        updates = _parse_scoped_variable_arguments(
            getattr(arguments, "substitution_variable_updates", ()),
            option_name="--set-subvar",
        )
        creations = _parse_scoped_variable_arguments(
            getattr(arguments, "substitution_variable_creations", ()),
            option_name="--create-subvar",
        )
        update_identities = {
            (scope.casefold(), name.casefold())
            for scope, name in updates
        }
        creation_identities = {
            (scope.casefold(), name.casefold())
            for scope, name in creations
        }
        duplicates = update_identities & creation_identities
        if duplicates:
            names = ", ".join(
                f"{scope}.{name}"
                for scope, name in sorted(duplicates)
            )
            raise ConfigurationError(
                "The same variable cannot be updated and created in one "
                f"command: {names}."
            )
        return

    if command == "history":
        unsupported = (
            arguments.metadata_file is not None,
            bool(arguments.inbox_file),
            bool(arguments.metadata_job_name),
            bool(arguments.rule_name),
            bool(arguments.runtime_prompts),
            bool(arguments.pipeline_code),
            bool(arguments.pipeline_variables),
            bool(arguments.pipeline_uploads),
            bool(arguments.pipeline_inbox_files),
            bool(getattr(arguments, "data_map_name", None)),
            bool(getattr(arguments, "cycle_code", None)),
            bool(getattr(arguments, "refresh_job", None)),
            bool(arguments.metadata_engine),
        )
        if any(unsupported):
            raise ConfigurationError(
                "The history command supports --execution-id and "
                "--history-limit only."
            )
        if getattr(arguments, "history_limit", 10) <= 0:
            raise ConfigurationError("--history-limit must be greater than zero.")
        return

    if command == "refresh-cube":
        unsupported = (
            arguments.metadata_file is not None,
            bool(arguments.inbox_file),
            bool(arguments.metadata_job_name),
            bool(arguments.rule_name),
            bool(arguments.runtime_prompts),
            bool(arguments.pipeline_code),
            bool(arguments.pipeline_variables),
            bool(arguments.pipeline_uploads),
            bool(arguments.pipeline_inbox_files),
            bool(getattr(arguments, "data_map_name", None)),
            bool(getattr(arguments, "cycle_code", None)),
            bool(getattr(arguments, "execution_id", None)),
            bool(arguments.start_period),
            bool(arguments.end_period),
        )
        if any(unsupported):
            raise ConfigurationError(
                "The refresh-cube command supports --refresh-job and "
                "--engine rest only."
            )
        if not getattr(arguments, "refresh_job", None):
            raise ConfigurationError(
                "The refresh-cube command requires --refresh-job."
            )
        if (
            arguments.metadata_engine
            and arguments.metadata_engine != "rest"
        ):
            raise ConfigurationError(
                "Cube Refresh is implemented through REST only."
            )
        return

    if command == "cycle":
        unsupported = (
            arguments.metadata_file is not None,
            bool(arguments.inbox_file),
            bool(arguments.metadata_job_name),
            bool(arguments.rule_name),
            bool(arguments.runtime_prompts),
            bool(arguments.import_mode),
            bool(arguments.export_mode),
            bool(arguments.error_file_name),
            bool(getattr(arguments, "execution_id", None)),
            bool(getattr(arguments, "refresh_job", None)),
            getattr(arguments, "refresh_after_metadata", None) is not None,
        )
        if any(unsupported):
            raise ConfigurationError(
                "The cycle command supports cycle values, Pipeline inputs, "
                "Data Map options, validation, and --engine rest only."
            )
        if (
            arguments.metadata_engine
            and arguments.metadata_engine != "rest"
        ):
            raise ConfigurationError(
                "Planning Cycle Control uses the REST engine."
            )
        if not getattr(arguments, "year", None):
            raise ConfigurationError(
                "The cycle command requires --year."
            )
        if not arguments.start_period or not arguments.end_period:
            raise ConfigurationError(
                "The cycle command requires --start-period and --end-period."
            )
        if getattr(arguments, "configure_cycle", False):
            raise ConfigurationError(
                "--configure-cycle is interactive only."
            )
        _parse_named_pipeline_files(
            arguments.pipeline_uploads,
            option_name="--pipeline-upload",
        )
        _parse_named_pipeline_files(
            arguments.pipeline_inbox_files,
            option_name="--pipeline-inbox",
        )
        return

    if command == "data-map":
        unsupported = (
            arguments.metadata_file is not None,
            bool(arguments.inbox_file),
            bool(arguments.metadata_job_name),
            bool(arguments.rule_name),
            bool(arguments.runtime_prompts),
            bool(arguments.pipeline_code),
            bool(arguments.pipeline_variables),
            bool(arguments.pipeline_uploads),
            bool(arguments.pipeline_inbox_files),
            bool(arguments.import_mode),
            bool(arguments.export_mode),
            bool(arguments.start_period),
            bool(arguments.end_period),
            bool(arguments.error_file_name),
        )
        if any(unsupported):
            raise ConfigurationError(
                "The data-map command supports --data-map, map overrides, "
                "target clearing, validation forms, and --engine only."
            )
        if not getattr(arguments, "data_map_name", None):
            raise ConfigurationError(
                "The data-map command requires --data-map."
            )
        if getattr(arguments, "skip_data_map", False):
            raise ConfigurationError(
                "--skip-data-map is only supported by the workflow command."
            )
        _parse_name_value_arguments(
            getattr(arguments, "data_map_overrides", ()),
            option_name="--map-override",
        )
        _parse_name_value_arguments(
            getattr(arguments, "data_map_exclusions", ()),
            option_name="--map-exclude",
        )
        return

    if command == "workflow":
        unsupported = (
            arguments.metadata_file is not None,
            bool(arguments.inbox_file),
            bool(arguments.metadata_job_name),
            bool(arguments.rule_name),
            bool(arguments.runtime_prompts),
            bool(arguments.import_mode),
            bool(arguments.export_mode),
            bool(arguments.start_period),
            bool(arguments.end_period),
            bool(arguments.error_file_name),
        )
        if any(unsupported):
            raise ConfigurationError(
                "The workflow command supports Pipeline inputs and "
                "variables, Data Map options, validation forms, and "
                "--engine only."
            )
        _parse_pipeline_variable_arguments(arguments.pipeline_variables)
        _parse_named_pipeline_files(
            arguments.pipeline_uploads,
            option_name="--pipeline-upload",
        )
        _parse_named_pipeline_files(
            arguments.pipeline_inbox_files,
            option_name="--pipeline-inbox",
        )
        _parse_name_value_arguments(
            getattr(arguments, "data_map_overrides", ()),
            option_name="--map-override",
        )
        _parse_name_value_arguments(
            getattr(arguments, "data_map_exclusions", ()),
            option_name="--map-exclude",
        )
        return

    if command == "login":
        if (
            _has_import_arguments(arguments)
            or arguments.rule_name
            or arguments.runtime_prompts
            or arguments.pipeline_code
            or arguments.pipeline_variables
            or arguments.pipeline_uploads
            or arguments.pipeline_inbox_files
        ):
            raise ConfigurationError(
                "Operation arguments cannot be used with the login command."
            )
        return

    if command == "pipeline":
        unsupported = (
            arguments.metadata_file is not None,
            bool(arguments.inbox_file),
            bool(arguments.metadata_job_name),
            bool(arguments.rule_name),
            bool(arguments.runtime_prompts),
            bool(arguments.import_mode),
            bool(arguments.export_mode),
            bool(arguments.start_period),
            bool(arguments.end_period),
            bool(arguments.error_file_name),
            bool(getattr(arguments, "data_map_name", None)),
            bool(getattr(arguments, "data_map_overrides", ())),
            bool(getattr(arguments, "data_map_exclusions", ())),
            getattr(arguments, "clear_target", None) is not None,
            bool(getattr(arguments, "skip_data_map", False)),
            bool(source_form),
            bool(target_form),
        )
        if any(unsupported):
            raise ConfigurationError(
                "The pipeline command supports --pipeline, repeated "
                "--variable/--param, and --engine; file, job, rule, period, "
                "and import arguments are not supported."
            )
        if not arguments.pipeline_code:
            raise ConfigurationError(
                "The pipeline command requires --pipeline."
            )
        _parse_pipeline_variable_arguments(arguments.pipeline_variables)
        _parse_named_pipeline_files(
            arguments.pipeline_uploads,
            option_name="--pipeline-upload",
        )
        _parse_named_pipeline_files(
            arguments.pipeline_inbox_files,
            option_name="--pipeline-inbox",
        )
        return

    if command == "rule":
        unsupported = (
            arguments.metadata_file is not None,
            bool(arguments.inbox_file),
            bool(arguments.metadata_job_name),
            bool(arguments.import_mode),
            bool(arguments.export_mode),
            bool(arguments.start_period),
            bool(arguments.end_period),
            bool(arguments.error_file_name),
            bool(arguments.pipeline_code),
            bool(arguments.pipeline_variables),
            bool(arguments.pipeline_uploads),
            bool(arguments.pipeline_inbox_files),
        )
        if any(unsupported):
            raise ConfigurationError(
                "The rule command supports --rule, repeated --rtp, and "
                "--engine; file, job, period, and import arguments are not "
                "supported."
            )
        if not arguments.rule_name:
            raise ConfigurationError(
                "The rule command requires --rule."
            )
        _parse_runtime_prompt_arguments(arguments.runtime_prompts)
        return

    if arguments.rule_name or arguments.runtime_prompts:
        raise ConfigurationError(
            "--rule and --rtp are only supported by the rule command."
        )
    if (
        arguments.pipeline_code
        or arguments.pipeline_variables
        or arguments.pipeline_uploads
        or arguments.pipeline_inbox_files
    ):
        raise ConfigurationError(
            "Pipeline file and variable options are only supported by the "
            "pipeline command."
        )
    if (
        getattr(arguments, "data_map_name", None)
        or getattr(arguments, "data_map_overrides", ())
        or getattr(arguments, "data_map_exclusions", ())
        or getattr(arguments, "clear_target", None) is not None
        or getattr(arguments, "skip_data_map", False)
        or source_form
        or target_form
    ):
        raise ConfigurationError(
            "Data Map and validation options are only supported by the "
            "data-map and workflow commands."
        )

    if command == "integration":
        has_local_file = arguments.metadata_file is not None
        has_inbox_file = bool(arguments.inbox_file)
        if has_local_file == has_inbox_file:
            raise ConfigurationError(
                "The integration command requires exactly one of --file or "
                "--inbox-file."
            )
        if bool(arguments.start_period) != bool(arguments.end_period):
            raise ConfigurationError(
                "The integration command requires both --start-period and "
                "--end-period."
            )
        if not arguments.start_period:
            raise ConfigurationError(
                "The integration command requires --start-period and "
                "--end-period."
            )
        if arguments.error_file_name:
            raise ConfigurationError(
                "--error-file-name is not supported by the integration "
                "command."
            )
        return

    operation = "metadata" if command == "metadata" else "data"
    has_local_file = arguments.metadata_file is not None
    has_inbox_file = bool(arguments.inbox_file)
    if has_local_file == has_inbox_file:
        raise ConfigurationError(
            f"The {operation} command requires exactly one of --file or "
            "--inbox-file."
        )
    if not arguments.metadata_job_name:
        raise ConfigurationError(
            f"The {operation} command requires --job."
        )
    if command == "data" and arguments.import_mode:
        raise ConfigurationError(
            "--import-mode is only supported by the metadata and integration "
            "commands."
        )
    if arguments.export_mode or arguments.start_period or arguments.end_period:
        raise ConfigurationError(
            "Period and export-mode arguments are only supported by the "
            "integration command."
        )


def _run_interactive_menu(
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> int:
    while True:
        output_func(
            "\nOracle EPM Planning Automation\n"
            "\n"
            "1. Check connection\n"
            "2. Load metadata\n"
            "3. Load data\n"
            "4. Run Data Integration / Data Management\n"
            "5. Run Business Rule\n"
            "6. Run Data Integration Pipeline\n"
            "7. Run Data Map\n"
            "8. Run Monthly Forecast Workflow\n"
            "9. Configure / Run Planning Cycle\n"
            "10. View Workflow History\n"
            "11. Refresh Planning Cube\n"
            "12. Manage Substitution Variables\n"
            "13. Generate Planning Form Report\n"
            "14. Run End-to-End Planning Process\n"
            "0. Exit\n"
        )
        selection = input_func("Select an option: ").strip()

        if selection == "0":
            output_func("Exiting Oracle EPM Planning Automation.")
            return 0
        if selection == "1":
            arguments = _interactive_arguments(command="login")
            return _run_command(
                "login",
                arguments,
                interactive=True,
                input_func=input_func,
                output_func=output_func,
            )
        if selection == "2":
            metadata_engine = _prompt_execution_engine(
                operation_name="Metadata",
                input_func=input_func,
                output_func=output_func,
            )
            if metadata_engine is None:
                continue
            metadata_file, inbox_file = _prompt_file_source(
                operation_name="metadata",
                supported_formats="CSV or ZIP",
                input_func=input_func,
                output_func=output_func,
            )
            if metadata_file is None and inbox_file is None:
                continue
            arguments = _interactive_arguments(
                command="metadata",
                metadata_file=metadata_file,
                inbox_file=inbox_file,
                metadata_engine=metadata_engine,
            )
            return _run_command(
                "metadata",
                arguments,
                interactive=True,
                input_func=input_func,
                output_func=output_func,
            )
        if selection == "3":
            data_engine = _prompt_execution_engine(
                operation_name="Data",
                input_func=input_func,
                output_func=output_func,
            )
            if data_engine is None:
                continue
            data_file, inbox_file = _prompt_file_source(
                operation_name="data",
                supported_formats="CSV, TXT, or ZIP",
                input_func=input_func,
                output_func=output_func,
            )
            if data_file is None and inbox_file is None:
                continue
            arguments = _interactive_arguments(
                command="data",
                metadata_file=data_file,
                inbox_file=inbox_file,
                metadata_engine=data_engine,
            )
            return _run_command(
                "data",
                arguments,
                interactive=True,
                input_func=input_func,
                output_func=output_func,
            )
        if selection == "4":
            integration_engine = _prompt_execution_engine(
                operation_name="Data Integration",
                input_func=input_func,
                output_func=output_func,
            )
            if integration_engine is None:
                continue
            data_file, inbox_file = _prompt_file_source(
                operation_name="Data Integration",
                supported_formats="source",
                preserve_inbox_reference=True,
                input_func=input_func,
                output_func=output_func,
            )
            if data_file is None and inbox_file is None:
                continue
            arguments = _interactive_arguments(
                command="integration",
                metadata_file=data_file,
                inbox_file=inbox_file,
                metadata_engine=integration_engine,
            )
            return _run_command(
                "integration",
                arguments,
                interactive=True,
                input_func=input_func,
                output_func=output_func,
            )
        if selection == "5":
            rule_engine = _prompt_execution_engine(
                operation_name="Business Rule",
                input_func=input_func,
                output_func=output_func,
            )
            if rule_engine is None:
                continue
            arguments = _interactive_arguments(
                command="rule",
                metadata_engine=rule_engine,
            )
            return _run_command(
                "rule",
                arguments,
                interactive=True,
                input_func=input_func,
                output_func=output_func,
            )
        if selection == "6":
            pipeline_engine = _prompt_execution_engine(
                operation_name="Pipeline",
                input_func=input_func,
                output_func=output_func,
            )
            if pipeline_engine is None:
                continue
            arguments = _interactive_arguments(
                command="pipeline",
                metadata_engine=pipeline_engine,
            )
            return _run_command(
                "pipeline",
                arguments,
                interactive=True,
                input_func=input_func,
                output_func=output_func,
            )
        if selection == "7":
            data_map_engine = _prompt_execution_engine(
                operation_name="Data Map",
                input_func=input_func,
                output_func=output_func,
            )
            if data_map_engine is None:
                continue
            arguments = _interactive_arguments(
                command="data-map",
                metadata_engine=data_map_engine,
            )
            return _run_command(
                "data-map",
                arguments,
                interactive=True,
                input_func=input_func,
                output_func=output_func,
            )
        if selection == "8":
            arguments = _interactive_arguments(
                command="workflow",
                metadata_engine="rest",
            )
            return _run_command(
                "workflow",
                arguments,
                interactive=True,
                input_func=input_func,
                output_func=output_func,
            )
        if selection == "9":
            arguments = _interactive_arguments(
                command="cycle",
                metadata_engine="rest",
            )
            return _run_command(
                "cycle",
                arguments,
                interactive=True,
                input_func=input_func,
                output_func=output_func,
            )
        if selection == "10":
            arguments = _interactive_arguments(command="history")
            return _run_command(
                "history",
                arguments,
                interactive=True,
                input_func=input_func,
                output_func=output_func,
            )
        if selection == "11":
            arguments = _interactive_arguments(
                command="refresh-cube",
                metadata_engine="rest",
            )
            return _run_command(
                "refresh-cube",
                arguments,
                interactive=True,
                input_func=input_func,
                output_func=output_func,
            )
        if selection == "12":
            arguments = _interactive_arguments(
                command="variables",
                metadata_engine="rest",
            )
            return _run_command(
                "variables",
                arguments,
                interactive=True,
                input_func=input_func,
                output_func=output_func,
            )
        if selection == "13":
            arguments = _interactive_arguments(
                command="report",
                metadata_engine="rest",
            )
            return _run_command(
                "report",
                arguments,
                interactive=True,
                input_func=input_func,
                output_func=output_func,
            )
        if selection == "14":
            arguments = _interactive_arguments(
                command="process",
                metadata_engine="rest",
            )
            return _run_command(
                "process",
                arguments,
                interactive=True,
                input_func=input_func,
                output_func=output_func,
            )

        output_func("Invalid selection. Enter a number from 0 to 14.")


def _interactive_arguments(
    *,
    command: str,
    metadata_file: Path | None = None,
    inbox_file: str | None = None,
    metadata_engine: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        command=command,
        metadata_file=metadata_file,
        inbox_file=inbox_file,
        metadata_job_name=None,
        rule_name=None,
        runtime_prompts=[],
        pipeline_code=None,
        pipeline_variables=[],
        pipeline_uploads=[],
        pipeline_inbox_files=[],
        data_map_name=None,
        data_map_overrides=[],
        data_map_exclusions=[],
        clear_target=None,
        skip_data_map=False,
        source_form=None,
        target_form=None,
        validation_tolerance=None,
        report_form=None,
        report_output=None,
        report_title=None,
        report_page_members=[],
        report_filter_members=[],
        overwrite_report=False,
        cycle_code=None,
        process_code=None,
        process_run_refresh=None,
        skip_report=False,
        process_dry_run=False,
        year=None,
        scenario=None,
        cycle_version=None,
        configure_cycle=False,
        execution_id=None,
        history_limit=10,
        refresh_job=None,
        refresh_after_metadata=None,
        substitution_variable_updates=[],
        substitution_variable_creations=[],
        import_mode=None,
        export_mode=None,
        start_period=None,
        end_period=None,
        error_file_name=None,
        metadata_engine=metadata_engine,
    )


def _prompt_execution_engine(
    *,
    operation_name: str,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> str | None:
    while True:
        output_func(
            f"\n{operation_name} execution method\n"
            "\n"
            "1. Oracle Planning REST API\n"
            "2. EPM Automate\n"
            "0. Back to main menu\n"
        )
        selection = input_func("Select an execution method: ").strip()
        if selection == "0":
            return None
        if selection == "1":
            return "rest"
        if selection == "2":
            return "epmautomate"
        output_func("Invalid selection. Enter 0, 1, or 2.")


def _prompt_file_source(
    *,
    operation_name: str,
    supported_formats: str,
    preserve_inbox_reference: bool = False,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> tuple[Path | None, str | None]:
    while True:
        output_func(
            f"\n{operation_name.title()} file source\n"
            "\n"
            f"1. Upload a local {supported_formats} file\n"
            f"2. Use a {supported_formats} file already in the Oracle Inbox\n"
            "0. Back to main menu\n"
        )
        selection = input_func("Select a file source: ").strip()

        if selection == "0":
            return None, None
        if selection == "1":
            raw_path = input_func(
                f"Drag and drop the {operation_name} file here, "
                "or enter its path: "
            )
            normalized_path = _normalize_dragged_path(raw_path)
            if not normalized_path:
                output_func(
                    f"A {operation_name} file path is required."
                )
                continue
            return Path(normalized_path), None
        if selection == "2":
            prompt = (
                "Enter the existing Oracle file reference "
                "(filename, inbox/..., or #epminbox/...): "
                if preserve_inbox_reference
                else "Enter the existing Oracle Inbox filename: "
            )
            file_name = input_func(prompt).strip()
            if not file_name:
                output_func("An Inbox filename is required.")
                continue
            if preserve_inbox_reference:
                return None, file_name
            return None, PurePath(file_name).name

        output_func("Invalid selection. Enter 0, 1, or 2.")


def _normalize_dragged_path(value: str) -> str:
    normalized = value.strip()
    if (
        len(normalized) >= 2
        and normalized[0] == normalized[-1]
        and normalized[0] in {'"', "'"}
    ):
        normalized = normalized[1:-1].strip()
    return normalized


def _build_task_notification(
    command: str,
    arguments: argparse.Namespace,
    *,
    settings: Settings,
    status: TaskNotificationStatus,
    duration_seconds: float,
    error: Exception | None = None,
) -> TaskNotificationEvent:
    """Build a provider-neutral terminal event from command context."""
    task_names = {
        "login": "Connection check",
        "metadata": "Metadata load",
        "data": "Native Planning data load",
        "integration": "Data Integration load",
        "rule": "Business Rule execution",
        "pipeline": "Data Integration Pipeline execution",
        "data-map": "Planning Data Map execution",
        "workflow": "Monthly Forecast workflow",
        "cycle": "Planning Cycle workflow",
        "refresh-cube": "Planning Cube Refresh",
        "history": "Workflow history query",
        "variables": "Substitution Variable maintenance",
        "report": "Planning form report generation",
        "process": "End-to-End Planning Process",
    }
    if command == "login":
        execution_engine = "rest"
    elif command == "metadata":
        execution_engine = (
            getattr(arguments, "_resolved_engine", None)
            or arguments.metadata_engine
            or settings.default_metadata_engine
        )
    elif command == "data":
        execution_engine = (
            getattr(arguments, "_resolved_engine", None)
            or arguments.metadata_engine
            or settings.default_data_engine
        )
    elif command == "integration":
        execution_engine = (
            getattr(arguments, "_resolved_engine", None)
            or arguments.metadata_engine
            or settings.default_data_integration_engine
        )
    elif command == "rule":
        execution_engine = (
            getattr(arguments, "_resolved_engine", None)
            or arguments.metadata_engine
            or settings.default_business_rule_engine
        )
    elif command in {"pipeline", "workflow"}:
        execution_engine = (
            getattr(arguments, "_resolved_engine", None)
            or arguments.metadata_engine
            or settings.default_pipeline_engine
        )
    elif command in {
        "cycle",
        "process",
        "refresh-cube",
        "variables",
        "report",
    }:
        execution_engine = "rest"
    elif command == "history":
        execution_engine = "local"
    else:
        execution_engine = (
            getattr(arguments, "_resolved_engine", None)
            or arguments.metadata_engine
            or settings.default_data_map_engine
        )

    pipeline_files = getattr(
        arguments,
        "_resolved_pipeline_files",
        (),
    )
    if command in {"report", "process"} and getattr(
        arguments,
        "report_output",
        None,
    ):
        file_name = Path(arguments.report_output).name
    elif command in {"pipeline", "workflow"} and pipeline_files:
        file_name = ", ".join(str(value) for value in pipeline_files)
    elif arguments.metadata_file is not None:
        file_name = arguments.metadata_file.name
    else:
        file_name = arguments.inbox_file

    if command == "rule":
        job_name = arguments.rule_name
    elif command == "pipeline":
        job_name = arguments.pipeline_code
    elif command == "data-map":
        job_name = arguments.data_map_name
    elif command == "workflow":
        job_name = (
            f"{arguments.pipeline_code or settings.default_pipeline_code} -> "
            f"{arguments.data_map_name or settings.default_data_map_name}"
        )
    elif command == "cycle":
        job_name = arguments.cycle_code or "MONTHLY_FORECAST"
    elif command == "process":
        job_name = (
            arguments.process_code or "MONTHLY_FORECAST_PROCESS"
        )
    elif command == "refresh-cube":
        job_name = arguments.refresh_job
    elif command == "history":
        job_name = arguments.execution_id
    elif command == "variables":
        changed_names = (
            list(arguments.substitution_variable_updates)
            + list(arguments.substitution_variable_creations)
        )
        job_name = ", ".join(changed_names) if changed_names else None
    elif command == "report":
        job_name = arguments.report_form
    else:
        job_name = arguments.metadata_job_name
    if command == "integration" and not job_name:
        job_name = settings.default_data_integration_name

    period_range = getattr(arguments, "_resolved_period_name", None)
    if period_range is None and arguments.start_period:
        end_period = arguments.end_period or arguments.start_period
        period_range = (
            f"{{{arguments.start_period}}}"
            if end_period == arguments.start_period
            else f"{{{arguments.start_period}}}{{{end_period}}}"
        )

    error_message = None
    if error is not None:
        error_message = " ".join(str(error).split())[:1_500]

    return TaskNotificationEvent(
        task_name=task_names.get(command, command),
        status=status,
        environment_url=settings.epm_base_url,
        application_name=settings.application_name,
        execution_engine=str(execution_engine),
        occurred_at=datetime.now().astimezone(),
        duration_seconds=max(duration_seconds, 0.0),
        job_or_integration_name=(
            str(job_name) if job_name else None
        ),
        file_name=(str(file_name) if file_name else None),
        period_range=(
            str(period_range) if period_range else None
        ),
        error_message=error_message,
    )


def _run_command(
    command: str,
    arguments: argparse.Namespace,
    *,
    interactive: bool,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> int:
    """Execute one command and publish its terminal notification."""
    settings = Settings.from_env()
    if isinstance(settings, Settings):
        assert_schema_current(
            settings.database_target,
            project_root=PROJECT_ROOT,
        )
    logger = configure_logging(settings.log_level)
    email_settings = settings.email_notifications
    if not isinstance(email_settings, EmailNotificationSettings):
        email_settings = EmailNotificationSettings()
    notifications = create_notification_service(
        email_settings,
        logger=logger.getChild("notification_service"),
    )
    started_at = time.monotonic()

    try:
        exit_code = _execute_command(
            command,
            arguments,
            settings=settings,
            logger=logger,
            interactive=interactive,
            input_func=input_func,
            output_func=output_func,
        )
    except Exception as exc:
        if command != "history":
            notifications.publish(
                _build_task_notification(
                    command,
                    arguments,
                    settings=settings,
                    status=TaskNotificationStatus.FAILED,
                    duration_seconds=time.monotonic() - started_at,
                    error=exc,
                )
            )
        raise

    if (
        exit_code == 0
        and not getattr(arguments, "_task_cancelled", False)
        and command != "history"
    ):
        notifications.publish(
            _build_task_notification(
                command,
                arguments,
                settings=settings,
                status=TaskNotificationStatus.SUCCESS,
                duration_seconds=time.monotonic() - started_at,
            )
        )
    return exit_code


def _execute_command(
    command: str,
    arguments: argparse.Namespace,
    *,
    settings: Settings,
    logger: logging.Logger,
    interactive: bool,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> int:
    """Execute a configured command without notification policy."""
    if not settings.verify_ssl:
        logger.warning("TLS certificate verification is disabled.")

    if command == "login":
        with EPMClient(
            settings,
            logger=logger.getChild("client"),
        ) as client:
            client.authenticate()
            application = ApplicationService(
                client,
                logger=logger.getChild("application_service"),
            ).get_configured_application()
            logger.info(
                "Planning application verified: name='%s', type='%s', "
                "storage='%s'.",
                application.name,
                application.application_type or "unknown",
                application.storage or "unknown",
            )
            output_func("Successfully connected to Oracle Planning.")
            return 0

    if command == "history":
        return _run_workflow_history_command(
            arguments,
            settings=settings,
            interactive=interactive,
            input_func=input_func,
            output_func=output_func,
        )

    if command == "variables":
        return _run_substitution_variable_command(
            arguments,
            settings=settings,
            interactive=interactive,
            input_func=input_func,
            output_func=output_func,
            logger=logger,
        )

    if command == "refresh-cube":
        return _run_cube_refresh_command(
            arguments,
            settings=settings,
            interactive=interactive,
            input_func=input_func,
            output_func=output_func,
            logger=logger,
        )

    if command == "report":
        return _run_form_report_command(
            arguments,
            settings=settings,
            interactive=interactive,
            input_func=input_func,
            output_func=output_func,
            logger=logger,
        )

    if command == "cycle":
        return _run_planning_cycle_command(
            arguments,
            settings=settings,
            interactive=interactive,
            input_func=input_func,
            output_func=output_func,
            logger=logger,
        )

    if command == "process":
        return _run_planning_process_command(
            arguments,
            settings=settings,
            interactive=interactive,
            input_func=input_func,
            output_func=output_func,
            logger=logger,
        )

    if command == "data-map":
        return _run_data_map_command(
            arguments,
            settings=settings,
            interactive=interactive,
            input_func=input_func,
            output_func=output_func,
            logger=logger,
        )

    if command == "workflow":
        return _run_monthly_forecast_workflow(
            arguments,
            settings=settings,
            interactive=interactive,
            input_func=input_func,
            output_func=output_func,
            logger=logger,
        )

    if command == "integration":
        return _run_data_integration_command(
            arguments,
            settings=settings,
            interactive=interactive,
            input_func=input_func,
            output_func=output_func,
            logger=logger,
        )

    if command == "rule":
        return _run_business_rule_command(
            arguments,
            settings=settings,
            interactive=interactive,
            input_func=input_func,
            output_func=output_func,
            logger=logger,
        )

    if command == "pipeline":
        return _run_pipeline_command(
            arguments,
            settings=settings,
            interactive=interactive,
            input_func=input_func,
            output_func=output_func,
            logger=logger,
        )

    operation_name = "Metadata" if command == "metadata" else "Data"
    job_type = "IMPORT_METADATA" if command == "metadata" else "IMPORT_DATA"
    default_engine = (
        settings.default_metadata_engine
        if command == "metadata"
        else settings.default_data_engine
    )
    execution_engine = arguments.metadata_engine or default_engine
    setattr(arguments, "_resolved_engine", execution_engine)

    if execution_engine == "rest":
        with EPMClient(
            settings,
            logger=logger.getChild("client"),
        ) as client:
            client.authenticate()
            output_func("Successfully connected to Oracle Planning.")
            job_service = JobService(
                client,
                logger=logger.getChild("job_service"),
            )
            prepared = _prepare_import_arguments(
                arguments,
                job_service=job_service,
                job_type=job_type,
                operation_name=operation_name,
                interactive=interactive,
                input_func=input_func,
                output_func=output_func,
            )
            if prepared is None:
                setattr(arguments, "_task_cancelled", True)
                return 0
            job_name, error_file_name = prepared
            arguments.metadata_job_name = job_name
            if command == "metadata":
                _execute_metadata_load(
                    client,
                    settings,
                    job_service=job_service,
                    metadata_file=arguments.metadata_file,
                    inbox_file_name=arguments.inbox_file,
                    job_name=job_name,
                    import_mode=(
                        arguments.import_mode
                        or settings.default_metadata_import_mode
                    ),
                    error_file_name=error_file_name,
                    logger=logger,
                    output_func=output_func,
                )
                _maybe_run_post_metadata_refresh(
                    arguments,
                    settings=settings,
                    interactive=interactive,
                    input_func=input_func,
                    output_func=output_func,
                    logger=logger,
                )
            else:
                _execute_data_load(
                    client,
                    settings,
                    job_service=job_service,
                    data_file=arguments.metadata_file,
                    inbox_file_name=arguments.inbox_file,
                    job_name=job_name,
                    error_file_name=error_file_name,
                    logger=logger,
                    output_func=output_func,
                )
            return 0

    if execution_engine != "epmautomate":
        raise ConfigurationError(
            f"Unsupported execution engine '{execution_engine}'."
        )

    if (
        command == "metadata"
        and arguments.import_mode not in {None, "job_definition"}
    ):
        raise ConfigurationError(
            "EPM Automate metadata imports use the saved job definition; "
            "--import-mode must be 'job_definition'."
        )

    job_name = str(arguments.metadata_job_name)
    error_file_name = arguments.error_file_name
    if interactive:
        with EPMClient(
            settings,
            logger=logger.getChild("job_discovery_client"),
        ) as client:
            client.authenticate()
            output_func(
                "Successfully connected to Oracle Planning for job discovery."
            )
            prepared = _prepare_import_arguments(
                arguments,
                job_service=JobService(
                    client,
                    logger=logger.getChild("job_service"),
                ),
                job_type=job_type,
                operation_name=operation_name,
                interactive=True,
                input_func=input_func,
                output_func=output_func,
            )
        if prepared is None:
            setattr(arguments, "_task_cancelled", True)
            return 0
        job_name, error_file_name = prepared
        arguments.metadata_job_name = job_name

    if command == "metadata":
        _execute_epm_automate_metadata_load(
            settings,
            metadata_file=arguments.metadata_file,
            inbox_file_name=arguments.inbox_file,
            job_name=job_name,
            error_file_name=error_file_name,
            logger=logger,
            output_func=output_func,
        )
        _maybe_run_post_metadata_refresh(
            arguments,
            settings=settings,
            interactive=interactive,
            input_func=input_func,
            output_func=output_func,
            logger=logger,
        )
    else:
        _execute_epm_automate_data_load(
            settings,
            data_file=arguments.metadata_file,
            inbox_file_name=arguments.inbox_file,
            job_name=job_name,
            error_file_name=error_file_name,
            logger=logger,
            output_func=output_func,
        )
    return 0


def _run_data_integration_command(
    arguments: argparse.Namespace,
    *,
    settings: Settings,
    interactive: bool,
    input_func: InputFunction,
    output_func: OutputFunction,
    logger: logging.Logger,
) -> int:
    """Resolve and execute a Data Integration workflow."""
    integration_name = (
        arguments.metadata_job_name
        or settings.default_data_integration_name
    )
    import_mode = (
        arguments.import_mode
        or settings.default_data_integration_import_mode
    )
    export_mode = (
        arguments.export_mode
        or settings.default_data_integration_export_mode
    )
    execution_engine = (
        arguments.metadata_engine
        or settings.default_data_integration_engine
    )
    setattr(arguments, "_resolved_engine", execution_engine)

    if interactive:
        integration_name = _prompt_integration_name(
            default_name=integration_name,
            catalog_file=settings.data_integration_catalog_file,
            input_func=input_func,
            output_func=output_func,
        )
        period_range = _prompt_integration_period_range(
            input_func=input_func,
            output_func=output_func,
        )
    else:
        if not integration_name:
            raise ConfigurationError(
                "Provide --integration-name or set "
                "DEFAULT_DATA_INTEGRATION_NAME."
            )
        period_range = DataIntegrationPeriodRange.from_period_names(
            str(arguments.start_period),
            str(arguments.end_period),
        )
    arguments.metadata_job_name = integration_name
    setattr(
        arguments,
        "_resolved_period_name",
        period_range.oracle_period_name,
    )

    normalized_import = DataIntegrationService.normalize_import_mode(
        import_mode
    )
    normalized_export = DataIntegrationService.normalize_export_mode(
        export_mode
    )
    if interactive and not _confirm_data_integration(
        arguments,
        integration_name=integration_name,
        period_range=period_range,
        import_mode=normalized_import,
        export_mode=normalized_export,
        engine=execution_engine,
        input_func=input_func,
        output_func=output_func,
    ):
        setattr(arguments, "_task_cancelled", True)
        output_func("Data Integration load cancelled.")
        return 0

    if execution_engine == "rest":
        with EPMClient(
            settings,
            logger=logger.getChild("client"),
        ) as client:
            client.authenticate()
            output_func("Successfully connected to Oracle Planning.")
            _execute_rest_data_integration(
                client,
                settings,
                data_file=arguments.metadata_file,
                inbox_file_name=arguments.inbox_file,
                integration_name=integration_name,
                period_range=period_range,
                import_mode=normalized_import,
                export_mode=normalized_export,
                logger=logger,
                output_func=output_func,
            )
        return 0

    if execution_engine == "epmautomate":
        _execute_epm_automate_data_integration(
            settings,
            data_file=arguments.metadata_file,
            inbox_file_name=arguments.inbox_file,
            integration_name=integration_name,
            period_range=period_range,
            import_mode=normalized_import,
            export_mode=normalized_export,
            logger=logger,
            output_func=output_func,
        )
        return 0

    raise ConfigurationError(
        f"Unsupported Data Integration engine '{execution_engine}'."
    )


def _run_business_rule_command(
    arguments: argparse.Namespace,
    *,
    settings: Settings,
    interactive: bool,
    input_func: InputFunction,
    output_func: OutputFunction,
    logger: logging.Logger,
) -> int:
    """Resolve, execute, and report one deployed Planning Business Rule."""
    execution_engine = (
        arguments.metadata_engine
        or settings.default_business_rule_engine
    )
    setattr(arguments, "_resolved_engine", execution_engine)
    runtime_prompts = _parse_runtime_prompt_arguments(
        arguments.runtime_prompts
    )
    rule_name = str(arguments.rule_name or "").strip()

    if execution_engine == "rest":
        with EPMClient(
            settings,
            logger=logger.getChild("client"),
        ) as client:
            client.authenticate()
            output_func("Successfully connected to Oracle Planning.")
            job_service = JobService(
                client,
                logger=logger.getChild("job_service"),
            )
            if interactive:
                prepared = _prepare_business_rule_arguments(
                    job_service,
                    input_func=input_func,
                    output_func=output_func,
                )
                if prepared is None:
                    setattr(arguments, "_task_cancelled", True)
                    output_func("Business Rule execution cancelled.")
                    return 0
                rule_name, runtime_prompts = prepared
                arguments.rule_name = rule_name
                arguments.runtime_prompts = [
                    f"{name}={value}"
                    for name, value in runtime_prompts.items()
                ]
            _execute_rest_business_rule(
                client,
                settings,
                job_service=job_service,
                rule_name=rule_name,
                runtime_prompts=runtime_prompts,
                logger=logger,
                output_func=output_func,
            )
        return 0

    if execution_engine != "epmautomate":
        raise ConfigurationError(
            f"Unsupported Business Rule engine '{execution_engine}'."
        )

    if interactive:
        with EPMClient(
            settings,
            logger=logger.getChild("rule_discovery_client"),
        ) as client:
            client.authenticate()
            output_func(
                "Successfully connected to Oracle Planning for rule "
                "discovery."
            )
            prepared = _prepare_business_rule_arguments(
                JobService(
                    client,
                    logger=logger.getChild("job_service"),
                ),
                input_func=input_func,
                output_func=output_func,
            )
        if prepared is None:
            setattr(arguments, "_task_cancelled", True)
            output_func("Business Rule execution cancelled.")
            return 0
        rule_name, runtime_prompts = prepared
        arguments.rule_name = rule_name
        arguments.runtime_prompts = [
            f"{name}={value}" for name, value in runtime_prompts.items()
        ]

    _execute_epm_automate_business_rule(
        settings,
        rule_name=rule_name,
        runtime_prompts=runtime_prompts,
        logger=logger,
        output_func=output_func,
    )
    return 0


def _prepare_business_rule_arguments(
    job_service: JobService,
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> tuple[str, dict[str, str]] | None:
    """Select a rule, collect optional prompts, and confirm execution."""
    rule_name = _select_business_rule(
        job_service,
        input_func=input_func,
        output_func=output_func,
    )
    if rule_name is None:
        return None
    runtime_prompts = _prompt_runtime_prompts(
        input_func=input_func,
        output_func=output_func,
    )
    if not _confirm_business_rule(
        rule_name,
        runtime_prompts,
        input_func=input_func,
        output_func=output_func,
    ):
        return None
    return rule_name, runtime_prompts


def _select_business_rule(
    job_service: JobService,
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> str | None:
    """List discoverable rules with manual-name and cancellation options."""
    try:
        definitions = job_service.get_job_definitions(job_type="RULES")
    except AuthenticationError as exc:
        if exc.status_code != 403:
            raise
        definitions = ()
        output_func(
            "Your user can authenticate but cannot list Planning job "
            "definitions. Enter the exact deployed Business Rule name "
            "manually."
        )
    output_func("\nAvailable Business Rules\n")
    for index, definition in enumerate(definitions, start=1):
        output_func(f"{index}. {definition.job_name}")

    manual_option = len(definitions) + 1
    output_func(f"{manual_option}. Enter another Business Rule name")
    output_func("0. Cancel")

    while True:
        raw_selection = input_func("Select a Business Rule: ").strip()
        try:
            selection = int(raw_selection)
        except ValueError:
            output_func("Enter the number shown beside the Business Rule.")
            continue

        if selection == 0:
            return None
        if 1 <= selection <= len(definitions):
            return definitions[selection - 1].job_name
        if selection == manual_option:
            return _prompt_manual_business_rule_name(
                input_func=input_func,
                output_func=output_func,
            )
        output_func(
            f"Enter a number from 0 to {manual_option}."
        )


def _prompt_manual_business_rule_name(
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> str:
    """Prompt until an exact non-empty Business Rule name is entered."""
    while True:
        rule_name = input_func(
            "Enter the exact deployed Business Rule name: "
        ).strip()
        if rule_name:
            return rule_name
        output_func("A Business Rule name is required.")


def _prompt_runtime_prompts(
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> dict[str, str]:
    """Collect optional case-sensitive runtime prompts from the user."""
    output_func(
        "\nRuntime prompts are optional when Calculation Manager defaults "
        "exist.\n"
        "Enter one NAME=VALUE pair at a time. Press Enter when finished."
    )
    prompts: dict[str, str] = {}
    while True:
        raw_value = input_func("Runtime prompt: ").strip()
        if not raw_value:
            return prompts
        try:
            parsed = _parse_runtime_prompt_arguments([raw_value])
        except ConfigurationError as exc:
            output_func(f"Invalid runtime prompt: {exc}")
            continue
        name, value = next(iter(parsed.items()))
        if name in prompts:
            output_func(
                f"Runtime prompt '{name}' was already entered."
            )
            continue
        prompts[name] = value


def _parse_runtime_prompt_arguments(
    values: Sequence[str],
) -> dict[str, str]:
    """Parse repeated NAME=VALUE arguments without splitting value equals."""
    prompts: dict[str, str] = {}
    for raw_value in values:
        if "=" not in raw_value:
            raise ConfigurationError(
                f"Runtime prompt '{raw_value}' must use NAME=VALUE."
            )
        name, value = raw_value.split("=", 1)
        normalized_name = name.strip()
        normalized_value = value.strip()
        if not normalized_name:
            raise ConfigurationError(
                "Runtime prompt name cannot be empty."
            )
        if not normalized_value:
            raise ConfigurationError(
                f"Runtime prompt '{normalized_name}' requires a value."
            )
        if normalized_name in prompts:
            raise ConfigurationError(
                f"Runtime prompt '{normalized_name}' was provided more "
                "than once."
            )
        prompts[normalized_name] = normalized_value
    return prompts


def _confirm_business_rule(
    rule_name: str,
    runtime_prompts: Mapping[str, str],
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> bool:
    """Display the exact Business Rule request before execution."""
    prompt_summary = (
        "\n".join(
            f"  {name} = {value}"
            for name, value in runtime_prompts.items()
        )
        if runtime_prompts
        else "  Oracle/Calculation Manager defaults"
    )
    output_func(
        "\nBusiness Rule execution summary\n"
        f"Rule: {rule_name}\n"
        "Runtime prompts:\n"
        f"{prompt_summary}"
    )
    confirmation = input_func("Continue? [y/N]: ").strip().casefold()
    return confirmation in {"y", "yes"}


def _execute_rest_business_rule(
    client: EPMClient,
    settings: Settings,
    *,
    job_service: JobService,
    rule_name: str,
    runtime_prompts: Mapping[str, str],
    logger: logging.Logger,
    output_func: OutputFunction,
) -> BusinessRuleSubmission:
    """Submit and monitor a Business Rule through Planning REST."""
    started_at = time.monotonic()
    service = BusinessRuleService(
        client,
        logger=logger.getChild("business_rule_service"),
    )
    monitor = JobMonitor(
        job_service,
        poll_interval=settings.default_poll_interval,
        timeout=settings.default_job_timeout,
        logger=logger.getChild("business_rule_monitor"),
    )
    submission = service.start_rule(
        rule_name,
        runtime_prompts=runtime_prompts,
    )
    final_job = monitor.wait_for_completion(submission.job_id)
    elapsed = time.monotonic() - started_at
    output_func(
        "Business Rule completed successfully using REST. "
        f"Job ID: {final_job.job_id}; "
        f"Rule: {submission.rule_name}; "
        f"Execution time: {elapsed:.2f} seconds."
    )
    return submission


def _execute_epm_automate_business_rule(
    settings: Settings,
    *,
    rule_name: str,
    runtime_prompts: Mapping[str, str],
    logger: logging.Logger,
    output_func: OutputFunction,
) -> None:
    """Execute one Business Rule through EPM Automate."""
    started_at = time.monotonic()
    password_file = settings.require_epm_automate_password_file()
    runner = EPMAutomateRunner(
        settings.epm_automate_executable,
        timeout=settings.epm_automate_command_timeout,
        logger=logger.getChild("epm_automate_runner"),
    )
    service = EPMAutomateBusinessRuleService(
        runner,
        username=settings.epm_username,
        password_file=password_file,
        base_url=settings.epm_base_url,
        logger=logger.getChild("epm_automate_business_rule_service"),
    )
    result = service.run_rule(
        rule_name,
        runtime_prompts=runtime_prompts,
    )
    elapsed = time.monotonic() - started_at
    output_func(
        "Business Rule completed successfully using EPM Automate. "
        f"Rule: {result.rule_name}; "
        f"Execution time: {elapsed:.2f} seconds."
    )


def _run_substitution_variable_command(
    arguments: argparse.Namespace,
    *,
    settings: Settings,
    interactive: bool,
    input_func: InputFunction,
    output_func: OutputFunction,
    logger: logging.Logger,
) -> int:
    """List, update, or explicitly create scoped variables through REST."""
    with EPMClient(
        settings,
        logger=logger.getChild("substitution_variable_client"),
    ) as client:
        client.authenticate()
        service = SubstitutionVariableService(
            client,
            logger=logger.getChild("substitution_variable_service"),
        )
        variables = service.get_all_variables()
        plan_types = service.get_plan_types()
        if interactive:
            modified = False
            while True:
                output_func(
                    "\nSubstitution Variable Maintenance\n"
                    "\n"
                    "1. List variables\n"
                    "2. Update an existing variable\n"
                    "3. Create a new variable\n"
                    "0. Return\n"
                )
                selection = input_func("Select an option: ").strip()
                if selection == "0":
                    if not modified:
                        setattr(arguments, "_task_cancelled", True)
                    return 0
                if selection == "1":
                    _display_substitution_variables(
                        variables,
                        output_func=output_func,
                    )
                    continue
                if selection == "2":
                    selected = _select_substitution_variable(
                        variables,
                        input_func=input_func,
                        output_func=output_func,
                    )
                    if selected is None:
                        continue
                    new_value = _prompt_required_text(
                        "New value",
                        input_func=input_func,
                        output_func=output_func,
                    )
                    updates = service.build_updates(
                        {(selected.scope, selected.name): new_value},
                        current_variables=variables,
                    )
                    update = updates[0]
                    output_func(
                        "\nSubstitution Variable update summary\n"
                        f"Scope: {update.scope}\n"
                        f"Name: {update.name}\n"
                        f"Current value: {update.old_value}\n"
                        f"New value: {update.new_value}"
                    )
                    if not _prompt_yes_no(
                        "Apply this update?",
                        default=False,
                        input_func=input_func,
                    ):
                        output_func("Variable update cancelled.")
                        continue
                    changed = service.apply_updates(updates)
                    variables = service.get_all_variables()
                    arguments.substitution_variable_updates.append(
                        f"{update.scope}.{update.name}={update.new_value}"
                    )
                    modified = modified or bool(changed)
                    output_func(
                        "Substitution variable updated and verified."
                        if changed
                        else "The variable already had the requested value."
                    )
                    continue
                if selection == "3":
                    scope = _select_substitution_variable_scope(
                        variables,
                        plan_types=plan_types,
                        input_func=input_func,
                        output_func=output_func,
                    )
                    if scope is None:
                        continue
                    name = _prompt_required_text(
                        "New variable name (without &)",
                        input_func=input_func,
                        output_func=output_func,
                    )
                    value = _prompt_required_text(
                        "Initial value",
                        input_func=input_func,
                        output_func=output_func,
                    )
                    output_func(
                        "\nCreate Substitution Variable summary\n"
                        f"Scope: {scope}\n"
                        f"Name: {name}\n"
                        f"Initial value: {value}\n"
                        "This creates a new Oracle application artifact."
                    )
                    if not _prompt_yes_no(
                        "Create this variable?",
                        default=False,
                        input_func=input_func,
                    ):
                        output_func("Variable creation cancelled.")
                        continue
                    created = service.create_variable(
                        scope,
                        name,
                        value,
                        current_variables=variables,
                    )
                    variables = service.get_all_variables()
                    arguments.substitution_variable_creations.append(
                        f"{created.scope}.{created.name}={created.value}"
                    )
                    modified = True
                    output_func(
                        "Substitution variable created and verified."
                    )
                    continue
                output_func("Invalid selection. Enter 0, 1, 2, or 3.")

        requested_updates = _parse_scoped_variable_arguments(
            arguments.substitution_variable_updates,
            option_name="--set-subvar",
        )
        requested_creations = _parse_scoped_variable_arguments(
            arguments.substitution_variable_creations,
            option_name="--create-subvar",
        )
        if not requested_updates and not requested_creations:
            _display_substitution_variables(
                variables,
                output_func=output_func,
            )
            setattr(arguments, "_task_cancelled", True)
            return 0

        updates = service.build_updates(
            requested_updates,
            current_variables=variables,
        )
        changed = service.apply_updates(updates)
        variables = service.get_all_variables()
        created_count = 0
        for (scope, name), value in requested_creations.items():
            created = service.create_variable(
                scope,
                name,
                value,
                current_variables=variables,
            )
            variables = (*variables, created)
            created_count += 1
        output_func(
            "Substitution Variable maintenance completed successfully. "
            f"Updated: {len(changed)}; Created: {created_count}."
        )
    return 0


def _display_substitution_variables(
    variables: Sequence[SubstitutionVariable],
    *,
    output_func: OutputFunction,
) -> None:
    """Display discovered variables with exact scope and current value."""
    output_func("\nCurrent Substitution Variables")
    if not variables:
        output_func("No substitution variables were discovered.")
        return
    for index, variable in enumerate(variables, start=1):
        output_func(
            f"{index}. {variable.scope}.{variable.name} = {variable.value}"
        )


def _select_substitution_variable(
    variables: Sequence[SubstitutionVariable],
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> SubstitutionVariable | None:
    """Select one existing scoped variable for an update."""
    _display_substitution_variables(variables, output_func=output_func)
    if not variables:
        return None
    output_func("0. Cancel")
    while True:
        raw_value = input_func(
            "Select the variable to update: "
        ).strip()
        if raw_value == "0":
            return None
        try:
            selected = int(raw_value)
        except ValueError:
            output_func("Enter one of the displayed option numbers.")
            continue
        if 1 <= selected <= len(variables):
            return variables[selected - 1]
        output_func("Enter one of the displayed option numbers.")


def _select_substitution_variable_scope(
    variables: Sequence[SubstitutionVariable],
    *,
    plan_types: Sequence[PlanType],
    input_func: InputFunction,
    output_func: OutputFunction,
) -> str | None:
    """Select ALL, a discovered cube, or an explicitly entered cube scope."""
    scopes = {
        variable.scope
        for variable in variables
        if variable.scope.casefold() != "all"
    }
    scopes.update(item.cube_name for item in plan_types)
    ordered_scopes = ["ALL", *sorted(scopes, key=str.casefold)]
    manual_option = len(ordered_scopes) + 1
    output_func("\nSubstitution Variable Scope")
    for index, scope in enumerate(ordered_scopes, start=1):
        label = "All cubes" if scope == "ALL" else f"Cube: {scope}"
        output_func(f"{index}. {label}")
    output_func(f"{manual_option}. Enter another cube name")
    output_func("0. Cancel")
    while True:
        raw_value = input_func("Select a scope: ").strip()
        if raw_value == "0":
            return None
        try:
            selected = int(raw_value)
        except ValueError:
            output_func("Enter one of the displayed option numbers.")
            continue
        if 1 <= selected <= len(ordered_scopes):
            return ordered_scopes[selected - 1]
        if selected == manual_option:
            return _prompt_required_text(
                "Exact cube name",
                input_func=input_func,
                output_func=output_func,
            )
        output_func("Enter one of the displayed option numbers.")


def _prompt_required_text(
    label: str,
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> str:
    """Prompt until a non-empty text value is supplied."""
    while True:
        value = input_func(f"{label}: ").strip()
        if value:
            return value
        output_func(f"{label} cannot be empty.")


def _run_planning_process_command(
    arguments: argparse.Namespace,
    *,
    settings: Settings,
    interactive: bool,
    input_func: InputFunction,
    output_func: OutputFunction,
    logger: logging.Logger,
) -> int:
    """Select an approved process and execute its associated cycle."""
    catalog = PlanningProcessCatalogService(settings.database_target)
    default_code = str(
        arguments.process_code or "MONTHLY_FORECAST_PROCESS"
    ).strip()
    if interactive:
        definitions = catalog.load(
            settings.planning_process_catalog_file
        )
        selected = _select_planning_process(
            definitions,
            default_code=default_code,
            input_func=input_func,
            output_func=output_func,
        )
        if selected is None:
            setattr(arguments, "_task_cancelled", True)
            output_func("Planning Process cancelled.")
            return 0
        definition = selected
    else:
        definition = catalog.get(
            settings.planning_process_catalog_file,
            default_code,
        )

    arguments.process_code = definition.code
    arguments.cycle_code = definition.cycle_code
    setattr(arguments, "_planning_process_definition", definition)
    setattr(arguments, "_process_preconfigured_cycle", True)
    return _run_planning_cycle_command(
        arguments,
        settings=settings,
        interactive=interactive,
        input_func=input_func,
        output_func=output_func,
        logger=logger,
    )


def _select_planning_process(
    definitions: Sequence[PlanningProcessDefinition],
    *,
    default_code: str | None,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> PlanningProcessDefinition | None:
    """Display configured end-to-end processes and return one selection."""
    default_index = next(
        (
            index
            for index, definition in enumerate(definitions, start=1)
            if default_code
            and definition.code.casefold() == default_code.casefold()
        ),
        None,
    )
    output_func("\nConfigured End-to-End Planning Processes")
    for index, definition in enumerate(definitions, start=1):
        output_func(
            f"{index}. {definition.display_name} "
            f"({definition.code})"
        )
    output_func("0. Cancel")
    while True:
        hint = f" [{default_index}]" if default_index else ""
        raw_value = input_func(
            f"Select a Planning Process{hint}: "
        ).strip()
        if not raw_value and default_index:
            return definitions[default_index - 1]
        if raw_value == "0":
            return None
        try:
            selected = int(raw_value)
        except ValueError:
            output_func("Enter one of the displayed option numbers.")
            continue
        if 1 <= selected <= len(definitions):
            return definitions[selected - 1]
        output_func("Enter one of the displayed option numbers.")


def _run_planning_cycle_command(
    arguments: argparse.Namespace,
    *,
    settings: Settings,
    interactive: bool,
    input_func: InputFunction,
    output_func: OutputFunction,
    logger: logging.Logger,
) -> int:
    """Configure, pre-flight, and run one REST-first Planning cycle."""
    catalog = PlanningCycleCatalogService(settings.database_target)
    cycle_code = str(
        arguments.cycle_code or "MONTHLY_FORECAST"
    ).strip()
    if interactive and not getattr(
        arguments,
        "_process_preconfigured_cycle",
        False,
    ):
        definitions = catalog.load(settings.planning_cycle_catalog_file)
        selected = _select_planning_cycle(
            definitions,
            default_code=cycle_code,
            input_func=input_func,
            output_func=output_func,
        )
        if selected is None:
            setattr(arguments, "_task_cancelled", True)
            output_func("Planning Cycle cancelled.")
            return 0
        definition = selected
        arguments.cycle_code = definition.code
    else:
        definition = catalog.get(
            settings.planning_cycle_catalog_file,
            cycle_code,
        )
        arguments.cycle_code = definition.code

    process_definition = getattr(
        arguments,
        "_planning_process_definition",
        None,
    )
    pipeline_only_process = _is_pipeline_only_process(process_definition)

    with EPMClient(
        settings,
        logger=logger.getChild("cycle_discovery_client"),
    ) as client:
        client.authenticate()
        variable_service = SubstitutionVariableService(
            client,
            logger=logger.getChild("substitution_variable_service"),
        )
        process_preconfigured = bool(
            getattr(arguments, "_process_preconfigured_cycle", False)
        )
        requested_variable_updates = tuple(
            getattr(arguments, "_process_variable_update_requests", ())
        )
        needs_variable_catalog = (
            bool(definition.variable_bindings)
            or bool(requested_variable_updates)
            or (interactive and not process_preconfigured)
        )
        variables = (
            variable_service.get_all_variables()
            if needs_variable_catalog
            else ()
        )
        plan_types = (
            variable_service.get_plan_types()
            if needs_variable_catalog
            else ()
        )
        if needs_variable_catalog:
            output_func(
                f"Discovered {len(variables)} substitution variables across "
                f"{len({item.scope for item in variables})} scope(s)."
            )
            if plan_types:
                output_func(
                    "Visible plan types: "
                    + ", ".join(item.cube_name for item in plan_types)
                )

        if interactive and not process_preconfigured and (
            arguments.configure_cycle
            or not definition.variable_bindings
            or _prompt_yes_no(
                "Reconfigure substitution-variable mappings?",
                default=False,
                input_func=input_func,
            )
        ):
            bindings = _prompt_cycle_variable_bindings(
                variables,
                input_func=input_func,
                output_func=output_func,
            )
            definition = catalog.save_bindings(
                settings.planning_cycle_catalog_file,
                definition.code,
                bindings,
            )
            output_func(
                "Planning cycle variable mappings saved successfully."
            )

        cycle = (
            _prompt_planning_cycle_values(
                definition,
                variables,
                input_func=input_func,
                output_func=output_func,
            )
            if interactive and not pipeline_only_process
            else PlanningCycle(
                year=str(arguments.year or "").strip(),
                start_period=str(arguments.start_period or "").strip(),
                end_period=str(arguments.end_period or "").strip(),
                scenario=(
                    str(arguments.scenario).strip()
                    if arguments.scenario
                    else None
                ),
                version=(
                    str(arguments.cycle_version).strip()
                    if arguments.cycle_version
                    else None
                ),
            )
        )
        arguments.year = cycle.year
        arguments.start_period = cycle.start_period
        arguments.end_period = cycle.end_period
        arguments.scenario = cycle.scenario
        arguments.cycle_version = cycle.version
        pipeline_service = PipelineService(
            client,
            logger=logger.getChild("cycle_pipeline_service"),
        )
        pipeline_details = pipeline_service.get_pipeline_details(
            definition.pipeline_code
        )
        data_maps = ()
        if definition.data_map_name:
            data_maps = tuple(
                item.job_name
                for item in JobService(client).get_job_definitions(
                    job_type="PLAN_TYPE_MAP"
                )
            )
        preflight = PlanningCyclePreflightService().validate(
            definition,
            cycle,
            variables=variables,
            pipeline_details=pipeline_details,
            available_data_maps=data_maps,
            variable_service=variable_service,
            require_cycle_values=not pipeline_only_process,
        )
        if requested_variable_updates:
            selected_updates = variable_service.build_safe_updates(
                requested_variable_updates,
                current_variables=variables,
            )
            resolved_variable_updates = variable_service.merge_updates(
                preflight.updates,
                selected_updates,
            )
        else:
            resolved_variable_updates = preflight.updates
        if isinstance(process_definition, PlanningProcessDefinition):
            report_step = _find_process_step(
                process_definition,
                PlanningProcessStepType.GENERATE_REPORT,
            )
            if (
                report_step is not None
                and report_step.enabled_by_default
                and not getattr(arguments, "skip_report", False)
            ):
                report_name = str(
                    report_step.parameters.get("reportName", "")
                ).strip()
                FormReportService(
                    client,
                    catalog_file=settings.report_catalog_file,
                    logger=logger.getChild(
                        "process_report_preflight_service"
                    ),
                ).get_form_layout(report_name)
                setattr(
                    arguments,
                    "_process_report_name",
                    report_name,
                )

    _display_planning_cycle_preflight(
        definition,
        cycle,
        resolved_variable_updates,
        preflight.pipeline_variables,
        output_func=output_func,
    )
    process_report_name = getattr(
        arguments,
        "_process_report_name",
        None,
    )
    if process_report_name:
        output_func(
            f"Process report validated: {process_report_name}"
        )
    if getattr(arguments, "process_dry_run", False):
        process_definition = getattr(
            arguments,
            "_planning_process_definition",
            None,
        )
        process_label = (
            process_definition.display_name
            if isinstance(process_definition, PlanningProcessDefinition)
            else definition.display_name
        )
        output_func(
            f"{process_label} preflight completed successfully. "
            "Dry run selected; no Oracle data or configuration was changed."
        )
        return 0
    if (
        interactive
        and not isinstance(
            getattr(arguments, "_planning_process_definition", None),
            PlanningProcessDefinition,
        )
        and not _prompt_yes_no(
            "Approve this Planning Cycle execution?",
            default=False,
            input_func=input_func,
        )
    ):
        setattr(arguments, "_task_cancelled", True)
        output_func("Planning Cycle cancelled before making changes.")
        return 0

    supplied_variables = _parse_pipeline_variable_arguments(
        arguments.pipeline_variables
    )
    supplied_variables.update(dict(preflight.pipeline_variables))
    arguments.pipeline_variables = [
        f"{name}={value}"
        for name, value in supplied_variables.items()
    ]
    arguments.pipeline_code = definition.pipeline_code
    arguments.data_map_name = definition.data_map_name
    arguments.metadata_engine = "rest"
    pipeline_periods = dict(preflight.pipeline_variables)
    resolved_start = pipeline_periods.get(
        "STARTPERIOD",
        cycle.pipeline_start_period,
    )
    resolved_end = pipeline_periods.get(
        "ENDPERIOD",
        cycle.pipeline_end_period,
    )
    setattr(
        arguments,
        "_resolved_period_name",
        (
            resolved_start
            if resolved_start == resolved_end
            else f"{resolved_start} to {resolved_end}"
        ),
    )
    setattr(arguments, "_cycle_preconfigured", True)
    setattr(
        arguments,
        "_cycle_variable_updates",
        tuple(
            update
            for update in resolved_variable_updates
            if update.is_changed
        ),
    )
    return _run_monthly_forecast_workflow(
        arguments,
        settings=settings,
        interactive=interactive,
        input_func=input_func,
        output_func=output_func,
        logger=logger,
    )


def _select_planning_cycle(
    definitions: Sequence[PlanningCycleDefinition],
    *,
    default_code: str | None,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> PlanningCycleDefinition | None:
    """Display configured Planning cycles and return one selection."""
    default_index = next(
        (
            index
            for index, item in enumerate(definitions, start=1)
            if default_code
            and item.code.casefold() == default_code.casefold()
        ),
        None,
    )
    output_func("\nConfigured Planning Cycles")
    for index, item in enumerate(definitions, start=1):
        output_func(
            f"{index}. {item.display_name} ({item.code})"
        )
    output_func("0. Cancel")
    while True:
        hint = f" [{default_index}]" if default_index else ""
        raw_value = input_func(
            f"Select a Planning Cycle{hint}: "
        ).strip()
        if not raw_value and default_index:
            return definitions[default_index - 1]
        if raw_value == "0":
            return None
        try:
            selected = int(raw_value)
        except ValueError:
            output_func("Enter one of the displayed option numbers.")
            continue
        if 1 <= selected <= len(definitions):
            return definitions[selected - 1]
        output_func("Enter one of the displayed option numbers.")


def _prompt_cycle_variable_bindings(
    variables: Sequence[SubstitutionVariable],
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> tuple[CycleVariableBinding, ...]:
    """Map zero or more discovered variables to each cycle value role."""
    if not variables:
        output_func(
            "No substitution variables were discovered; no mappings saved."
        )
        return ()
    output_func(
        "\nDiscovered substitution variables\n"
        "A role may update multiple variables. Enter comma-separated "
        "numbers, or press Enter to leave a role unmapped."
    )
    for index, variable in enumerate(variables, start=1):
        output_func(
            f"{index}. {variable.scope}.{variable.name} = {variable.value}"
        )

    bindings: list[CycleVariableBinding] = []
    used_indices: set[int] = set()
    for role in CycleValueRole:
        while True:
            raw_value = input_func(
                f"Variables for {role.display_name}: "
            ).strip()
            if not raw_value:
                break
            try:
                indices = [
                    int(value.strip())
                    for value in raw_value.split(",")
                    if value.strip()
                ]
            except ValueError:
                output_func("Enter comma-separated variable numbers.")
                continue
            if (
                not indices
                or any(index < 1 or index > len(variables) for index in indices)
            ):
                output_func("One or more variable numbers are invalid.")
                continue
            duplicates = used_indices & set(indices)
            if duplicates:
                output_func(
                    "A substitution variable cannot be mapped to multiple "
                    "cycle roles."
                )
                continue
            used_indices.update(indices)
            bindings.extend(
                CycleVariableBinding(
                    role=role,
                    variable_name=variables[index - 1].name,
                    scope=variables[index - 1].scope,
                )
                for index in indices
            )
            break
    return tuple(bindings)


def _prompt_planning_cycle_values(
    definition: PlanningCycleDefinition,
    variables: Sequence[SubstitutionVariable],
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> PlanningCycle:
    """Prompt for cycle values using current mapped values as defaults."""
    current = {
        (item.scope.casefold(), item.name.casefold()): item.value
        for item in variables
    }
    defaults: dict[CycleValueRole, str] = {}
    required_roles = {
        CycleValueRole.YEAR,
        CycleValueRole.START_PERIOD,
        CycleValueRole.END_PERIOD,
    }
    for binding in definition.variable_bindings:
        value = current.get(
            (
                binding.scope.casefold(),
                binding.variable_name.casefold(),
            )
        )
        if value and binding.role not in defaults:
            defaults[binding.role] = value
        required_roles.add(binding.role)

    def prompt(role: CycleValueRole) -> str | None:
        default = defaults.get(role)
        while True:
            hint = f" [{default}]" if default else ""
            value = input_func(
                f"{role.display_name}{hint}: "
            ).strip()
            resolved = value or default
            if resolved:
                return resolved
            if role not in required_roles:
                return None
            output_func(f"{role.display_name} is required.")

    output_func("\nPlanning Cycle runtime values")
    return PlanningCycle(
        year=str(prompt(CycleValueRole.YEAR)),
        start_period=str(prompt(CycleValueRole.START_PERIOD)),
        end_period=str(prompt(CycleValueRole.END_PERIOD)),
        scenario=prompt(CycleValueRole.SCENARIO),
        version=prompt(CycleValueRole.VERSION),
    )


def _display_planning_cycle_preflight(
    definition: PlanningCycleDefinition,
    cycle: PlanningCycle,
    updates: Sequence[SubstitutionVariableUpdate],
    pipeline_variables: Sequence[tuple[str, str]],
    *,
    output_func: OutputFunction,
) -> None:
    """Display all state changes and workflow inputs before approval."""
    change_lines = [
        (
            f"  {item.scope}.{item.name}: "
            f"{item.old_value} -> {item.new_value}"
        )
        for item in updates
    ] or ["  None"]
    pipeline_lines = [
        f"  {name}={value}" for name, value in pipeline_variables
    ] or ["  Oracle defaults / later prompts"]
    output_func(
        "\nPlanning Cycle pre-flight successful\n"
        f"Cycle: {definition.display_name} ({definition.code})\n"
        f"Year: {cycle.year}\n"
        f"Period: {cycle.start_period} to {cycle.end_period}\n"
        f"Scenario: {cycle.scenario or 'Not supplied'}\n"
        f"Version: {cycle.version or 'Not supplied'}\n"
        f"Pipeline: {definition.pipeline_code}\n"
        f"Data Map: {definition.data_map_name or 'Disabled'}\n"
        "Substitution-variable changes:\n"
        + "\n".join(change_lines)
        + "\nPipeline variables:\n"
        + "\n".join(pipeline_lines)
    )


def _run_workflow_history_command(
    arguments: argparse.Namespace,
    *,
    settings: Settings,
    interactive: bool,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> int:
    """Display recent workflow runs or one execution with step details."""
    repository = SQLWorkflowRepository(
        settings.database_target
    )
    execution_id = str(arguments.execution_id or "").strip()
    if execution_id:
        run = repository.get(execution_id)
        if run is None:
            raise ConfigurationError(
                f"Workflow execution '{execution_id}' was not found."
            )
        _display_workflow_run(run, output_func=output_func)
        return 0

    runs = repository.list_recent(limit=arguments.history_limit)
    if not runs:
        output_func("No workflow history is available.")
        return 0
    output_func("\nRecent Workflow Runs")
    for run in runs:
        output_func(
            f"{run.execution_id} | {run.workflow_name} | "
            f"{run.status.value} | {run.started_at.isoformat()}"
        )
    if interactive:
        selected = input_func(
            "Enter an execution ID for details, or press Enter to return: "
        ).strip()
        if selected:
            run = repository.get(selected)
            if run is None:
                raise ConfigurationError(
                    f"Workflow execution '{selected}' was not found."
                )
            _display_workflow_run(run, output_func=output_func)
    return 0


def _display_workflow_run(
    run: WorkflowRun,
    *,
    output_func: OutputFunction,
) -> None:
    """Display a complete stored workflow execution."""
    output_func(
        "\nWorkflow Run Details\n"
        f"Execution ID: {run.execution_id}\n"
        f"Workflow: {run.workflow_name}\n"
        f"Status: {run.status.value}\n"
        f"Started: {run.started_at.isoformat()}\n"
        f"Completed: "
        f"{run.completed_at.isoformat() if run.completed_at else 'Running'}"
    )
    for step in run.steps:
        details = json.dumps(step.details, default=str)
        output_func(
            f"{step.sequence}. {step.name}: {step.status.value}; "
            f"details={details}; error={step.error_message or 'None'}"
        )


def _run_cube_refresh_command(
    arguments: argparse.Namespace,
    *,
    settings: Settings,
    interactive: bool,
    input_func: InputFunction,
    output_func: OutputFunction,
    logger: logging.Logger,
) -> int:
    """Discover and execute an existing saved Cube Refresh job via REST."""
    with EPMClient(
        settings,
        logger=logger.getChild("cube_refresh_client"),
    ) as client:
        client.authenticate()
        job_service = JobService(
            client,
            logger=logger.getChild("job_service"),
        )
        definitions = job_service.get_job_definitions(
            job_type="CUBE_REFRESH"
        )
        if not definitions and interactive:
            output_func(
                "Oracle did not return any Cube Refresh job definitions. "
                "You can still enter the exact saved job name manually."
            )
        available = {item.job_name.casefold(): item for item in definitions}
        if interactive:
            selected = _select_saved_job_definition(
                definitions,
                title="Available Cube Refresh Jobs",
                default_name=arguments.refresh_job,
                input_func=input_func,
                output_func=output_func,
            )
            if selected is None:
                setattr(arguments, "_task_cancelled", True)
                output_func("Cube Refresh cancelled.")
                return 0
            job_name = selected
            if not _prompt_yes_no(
                f"Run Cube Refresh job '{job_name}'?",
                default=False,
                input_func=input_func,
            ):
                setattr(arguments, "_task_cancelled", True)
                output_func("Cube Refresh cancelled.")
                return 0
        else:
            job_name = str(arguments.refresh_job).strip()
            discovered = available.get(job_name.casefold())
            if discovered is not None:
                job_name = discovered.job_name
            else:
                logger.warning(
                    "Cube Refresh job '%s' was not returned by job "
                    "discovery; submitting the exact configured name "
                    "because some Planning environments omit saved refresh "
                    "jobs from this endpoint.",
                    job_name,
                )
        arguments.refresh_job = job_name
        service = CubeRefreshService(
            client,
            logger=logger.getChild("cube_refresh_service"),
        )
        monitor = JobMonitor(
            service,
            poll_interval=settings.default_poll_interval,
            timeout=settings.default_job_timeout,
            logger=logger.getChild("cube_refresh_monitor"),
        )
        started_at = time.monotonic()
        submission = service.start_refresh(job_name)
        final_job = monitor.wait_for_completion(submission.job_id)
    output_func(
        "Cube Refresh completed successfully using REST. "
        f"Job ID: {final_job.job_id}; Job: {job_name}; "
        f"Execution time: {time.monotonic() - started_at:.2f} seconds."
    )
    return 0


def _run_form_report_command(
    arguments: argparse.Namespace,
    *,
    settings: Settings,
    interactive: bool,
    input_func: InputFunction,
    output_func: OutputFunction,
    logger: logging.Logger,
) -> int:
    """Export a Planning form slice and render a local Excel report."""
    form_name = str(arguments.report_form or "").strip()

    with EPMClient(
        settings,
        logger=logger.getChild("report_client"),
    ) as client:
        client.authenticate()
        service = FormReportService(
            client,
            catalog_file=settings.report_catalog_file,
            logger=logger.getChild("report_service"),
        )
        if interactive and not form_name:
            form_name = _prompt_report_name(
                service.list_registered_reports(),
                input_func=input_func,
                output_func=output_func,
            )

        try:
            layout = service.get_form_layout(form_name)
        except ReportGenerationError as exc:
            if not interactive:
                raise
            output_func(
                "\nAutomatic Planning form discovery is unavailable in "
                f"this environment.\n{exc}"
            )
            if not _prompt_yes_no(
                f"Register '{form_name}' for data-slice reporting now?",
                default=True,
                input_func=input_func,
            ):
                setattr(arguments, "_task_cancelled", True)
                output_func("Planning form report generation cancelled.")
                return 0
            definition = _prompt_report_registration(
                form_name,
                input_func=input_func,
                output_func=output_func,
            )
            if definition is None:
                setattr(arguments, "_task_cancelled", True)
                output_func("Planning form report generation cancelled.")
                return 0
            registered = service.register_data_slice_definition(definition)
            layout = service.get_form_layout(registered.name)
            output_func(
                f"Report '{registered.name}' was registered in "
                f"'{settings.report_catalog_file}'."
            )

        overrides = tuple(
            _parse_name_value_arguments(
                arguments.report_page_members,
                option_name="--page-member",
            ).items()
        )
        filters = tuple(
            str(value).strip()
            for value in arguments.report_filter_members
            if str(value).strip()
        )
        if interactive:
            overrides = _prompt_report_page_members(
                layout,
                input_func=input_func,
                output_func=output_func,
            )
            if service.uses_data_slice_definition(form_name):
                filters = ()
                output_func(
                    "Row and column filters are controlled by the report "
                    "catalog for this environment."
                )
            else:
                filters = _prompt_report_filter_members(
                    input_func=input_func,
                    output_func=output_func,
                )

        default_title = service.get_default_title(form_name)
        title = str(arguments.report_title or "").strip() or default_title
        if interactive:
            entered_title = input_func(
                f"Report title [{default_title}]: "
            ).strip()
            title = entered_title or default_title

        output_path = arguments.report_output or _default_report_output_path(
            settings.report_output_dir,
            form_name,
        )
        if interactive:
            entered_path = input_func(
                f"Excel output path [{output_path}]: "
            )
            normalized_path = _normalize_dragged_path(entered_path)
            if normalized_path:
                output_path = Path(normalized_path)

        overwrite = bool(arguments.overwrite_report)
        if interactive and output_path.exists():
            overwrite = _prompt_yes_no(
                f"Report file '{output_path}' already exists. Replace it?",
                default=False,
                input_func=input_func,
            )
            if not overwrite:
                setattr(arguments, "_task_cancelled", True)
                output_func("Planning form report generation cancelled.")
                return 0

        _display_form_report_summary(
            form_name=form_name,
            title=title,
            output_path=output_path,
            layout=layout,
            overrides=overrides,
            filters=filters,
            output_func=output_func,
        )
        if interactive and not _prompt_yes_no(
            "Generate this Excel report?",
            default=False,
            input_func=input_func,
        ):
            setattr(arguments, "_task_cancelled", True)
            output_func("Planning form report generation cancelled.")
            return 0

        request = FormReportRequest(
            form_name=form_name,
            output_path=output_path,
            title=title,
            page_member_overrides=overrides,
            filter_members=filters,
            overwrite=overwrite,
        )
        result = service.generate(request, layout=layout)

    arguments.report_form = form_name
    arguments.report_output = result.output_path
    arguments.report_title = title
    output_func(
        "Planning form report generated successfully. "
        f"Form: {result.form_name}; Rows: {result.row_count}; "
        f"Data cells: {result.data_cell_count}; "
        f"Output: {result.output_path}"
    )
    return 0


def _prompt_report_name(
    definitions: Sequence[DataSliceReportDefinition],
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> str:
    """Select a registered report or accept a new Planning form name."""
    if definitions:
        output_func("\nRegistered Planning Reports")
        for index, definition in enumerate(definitions, start=1):
            output_func(
                f"{index}. {definition.name} ({definition.cube})"
            )
        output_func(
            "Enter a report number, or type a new Planning form name."
        )
    while True:
        entered = input_func(
            "Planning form or report name: "
        ).strip()
        if not entered:
            output_func("A Planning form name is required.")
            continue
        if entered.isdigit() and definitions:
            selection = int(entered)
            if 1 <= selection <= len(definitions):
                return definitions[selection - 1].name
            output_func(
                f"Select a number from 1 to {len(definitions)}, or type "
                "the exact form name."
            )
            continue
        return entered


def _prompt_report_registration(
    form_name: str,
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> DataSliceReportDefinition | None:
    """Collect one reusable Export Data Slice report definition."""
    output_func(
        "\nOne-time report registration\n"
        "The form name is retained as the report name. Configure the cube, "
        "default POV, columns, and rows exactly as they exist in Planning.\n"
        "Use | between member selections so member names may contain commas."
    )
    default_title = f"{form_name} Report"
    title = input_func(f"Report title [{default_title}]: ").strip()
    cube = _prompt_required_report_value(
        "Cube / plan type: ",
        "A cube or plan type is required.",
        input_func=input_func,
        output_func=output_func,
    )
    pov = _prompt_report_registration_pov(
        input_func=input_func,
        output_func=output_func,
    )
    assigned_dimensions = {
        dimension.casefold()
        for dimension, _ in pov
    }
    columns = _prompt_report_registration_axis(
        "column",
        assigned_dimensions=assigned_dimensions,
        input_func=input_func,
        output_func=output_func,
    )
    assigned_dimensions.update(
        dimension.casefold()
        for dimension in columns[0].dimensions
    )
    rows = _prompt_report_registration_axis(
        "row",
        assigned_dimensions=assigned_dimensions,
        input_func=input_func,
        output_func=output_func,
    )
    definition = DataSliceReportDefinition(
        name=form_name,
        cube=cube,
        title=title or default_title,
        pov=pov,
        columns=columns,
        rows=rows,
    )
    _display_report_registration_summary(
        definition,
        output_func=output_func,
    )
    if not _prompt_yes_no(
        "Save this report definition?",
        default=False,
        input_func=input_func,
    ):
        return None
    return definition


def _prompt_report_registration_pov(
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> tuple[tuple[str, str], ...]:
    """Collect optional default POV dimension/member pairs."""
    output_func(
        "\nDefault POV\n"
        "Enter each page dimension and its default member. Leave the "
        "dimension blank when finished."
    )
    pov: list[tuple[str, str]] = []
    seen: set[str] = set()
    while True:
        dimension = input_func("POV dimension: ").strip()
        if not dimension:
            return tuple(pov)
        key = dimension.casefold()
        if key in seen:
            output_func(
                f"POV dimension '{dimension}' was already entered."
            )
            continue
        member = _prompt_required_report_value(
            f"Default member for {dimension}: ",
            f"A default member is required for {dimension}.",
            input_func=input_func,
            output_func=output_func,
        )
        seen.add(key)
        pov.append((dimension, member))


def _prompt_report_registration_axis(
    axis_name: str,
    *,
    assigned_dimensions: set[str],
    input_func: InputFunction,
    output_func: OutputFunction,
) -> tuple[ReportAxisSegment, ...]:
    """Collect one rectangular row or column data-slice segment."""
    label = axis_name.capitalize()
    output_func(f"\n{label} axis")
    while True:
        raw_dimensions = input_func(
            f"{label} dimensions (comma-separated): "
        )
        dimensions = tuple(
            item.strip()
            for item in raw_dimensions.split(",")
            if item.strip()
        )
        normalized = [dimension.casefold() for dimension in dimensions]
        if not dimensions:
            output_func(
                f"At least one {axis_name} dimension is required."
            )
            continue
        if len(normalized) != len(set(normalized)):
            output_func(
                f"{label} dimensions cannot contain duplicates."
            )
            continue
        conflicts = [
            dimension
            for dimension in dimensions
            if dimension.casefold() in assigned_dimensions
        ]
        if conflicts:
            output_func(
                "Each dimension can belong to only one report axis. "
                f"Already assigned: {', '.join(conflicts)}."
            )
            continue
        break

    member_selections: list[tuple[str, ...]] = []
    for dimension in dimensions:
        while True:
            raw_members = input_func(
                f"{dimension} members (separate with |): "
            )
            members = tuple(
                member.strip()
                for member in raw_members.split("|")
                if member.strip()
            )
            if members:
                member_selections.append(members)
                break
            output_func(
                f"At least one member is required for {dimension}."
            )
    return (
        ReportAxisSegment(
            dimensions=dimensions,
            members=tuple(member_selections),
        ),
    )


def _prompt_required_report_value(
    prompt: str,
    error_message: str,
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> str:
    """Prompt until a required report-registration value is supplied."""
    while True:
        value = input_func(prompt).strip()
        if value:
            return value
        output_func(error_message)


def _display_report_registration_summary(
    definition: DataSliceReportDefinition,
    *,
    output_func: OutputFunction,
) -> None:
    """Show the catalog definition before it is persisted."""
    pov = ", ".join(
        f"{dimension}={member}"
        for dimension, member in definition.pov
    ) or "None"
    column_dimensions = ", ".join(definition.column_dimensions)
    row_dimensions = ", ".join(definition.row_dimensions)
    output_func(
        "\nReport registration summary\n"
        f"Name: {definition.name}\n"
        f"Title: {definition.title}\n"
        f"Cube: {definition.cube}\n"
        f"Default POV: {pov}\n"
        f"Column dimensions: {column_dimensions}\n"
        f"Row dimensions: {row_dimensions}"
    )


def _prompt_report_page_members(
    layout: FormLayout,
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> tuple[tuple[str, str], ...]:
    """Collect page POV members in the form's documented dimension order."""
    if not layout.page_dimensions:
        output_func("The Planning form has no page dimensions.")
        return ()

    pov = {
        dimension.casefold(): member
        for dimension, member in layout.pov
    }
    allowed = {
        dimension.casefold(): members
        for dimension, members in layout.allowed_page_members
    }
    overrides: list[tuple[str, str]] = []
    output_func("\nPlanning form page POV")
    for dimension in layout.page_dimensions:
        key = dimension.casefold()
        default_member = pov.get(key)
        allowed_members = allowed.get(key, ())
        if allowed_members:
            preview = ", ".join(allowed_members[:10])
            suffix = (
                f", ... ({len(allowed_members)} available)"
                if len(allowed_members) > 10
                else ""
            )
            output_func(
                f"{dimension} allowed members: {preview}{suffix}"
            )
        while True:
            hint = f" [{default_member}]" if default_member else ""
            member = input_func(
                f"{dimension} page member{hint}: "
            ).strip()
            resolved = member or default_member
            if not resolved:
                output_func(
                    f"A page member is required for {dimension}."
                )
                continue
            if allowed_members:
                canonical = next(
                    (
                        item
                        for item in allowed_members
                        if item.casefold() == resolved.casefold()
                    ),
                    None,
                )
                if canonical is None:
                    output_func(
                        f"'{resolved}' is not an allowed member for "
                        f"{dimension}."
                    )
                    continue
                resolved = canonical
            if resolved != default_member:
                overrides.append((dimension, resolved))
            break
    return tuple(overrides)


def _prompt_report_filter_members(
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> tuple[str, ...]:
    """Collect optional additional form filters one member at a time."""
    output_func(
        "\nOptional report filters\n"
        "Enter one member at a time. Press Enter when finished."
    )
    filters: list[str] = []
    while True:
        member = input_func("Filter member: ").strip()
        if not member:
            return tuple(filters)
        filters.append(member)


def _default_report_output_path(
    output_directory: Path,
    form_name: str,
) -> Path:
    """Build a unique, filesystem-safe default report path."""
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", form_name).strip("._")
    if not safe_name:
        safe_name = "Planning_Form_Report"
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    return Path(output_directory) / f"{safe_name}_{timestamp}.xlsx"


def _display_form_report_summary(
    *,
    form_name: str,
    title: str,
    output_path: Path,
    layout: FormLayout,
    overrides: Sequence[tuple[str, str]],
    filters: Sequence[str],
    output_func: OutputFunction,
) -> None:
    """Display all material report inputs before file creation."""
    current_pov = ", ".join(
        f"{dimension}={member}"
        for dimension, member in layout.pov
    ) or "Form defaults"
    page_overrides = ", ".join(
        f"{dimension}={member}"
        for dimension, member in overrides
    ) or "None"
    filter_summary = ", ".join(filters) or "None"
    output_func(
        "\nPlanning form report summary\n"
        f"Form: {form_name}\n"
        f"Title: {title}\n"
        f"Current form POV: {current_pov}\n"
        f"Page overrides: {page_overrides}\n"
        f"Additional filters: {filter_summary}\n"
        f"Output: {output_path}"
    )


def _maybe_run_post_metadata_refresh(
    arguments: argparse.Namespace,
    *,
    settings: Settings,
    interactive: bool,
    input_func: InputFunction,
    output_func: OutputFunction,
    logger: logging.Logger,
) -> None:
    """Optionally run a separately monitored REST refresh after metadata."""
    requested = arguments.refresh_after_metadata
    if interactive and requested is None:
        requested = _prompt_yes_no(
            "Metadata succeeded. Refresh the Planning cube now?",
            default=False,
            input_func=input_func,
        )
    if not requested:
        return
    if not interactive and not arguments.refresh_job:
        raise ConfigurationError(
            "--refresh-job is required with --refresh-after-metadata."
        )
    refresh_arguments = argparse.Namespace(**vars(arguments))
    refresh_arguments.command = "refresh-cube"
    _run_cube_refresh_command(
        refresh_arguments,
        settings=settings,
        interactive=interactive,
        input_func=input_func,
        output_func=output_func,
        logger=logger,
    )


def _select_saved_job_definition(
    definitions: Sequence[JobDefinition],
    *,
    title: str,
    default_name: str | None,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> str | None:
    """Select a discovered job or collect an exact saved job name."""
    default_index = next(
        (
            index
            for index, item in enumerate(definitions, start=1)
            if default_name
            and item.job_name.casefold() == default_name.casefold()
        ),
        None,
    )
    output_func(f"\n{title}")
    for index, item in enumerate(definitions, start=1):
        output_func(f"{index}. {item.job_name}")
    manual_option = len(definitions) + 1
    output_func(f"{manual_option}. Enter a saved job name manually")
    output_func("0. Cancel")
    while True:
        hint = f" [{default_index}]" if default_index else ""
        raw_value = input_func(f"Select a job{hint}: ").strip()
        if not raw_value and default_index:
            return definitions[default_index - 1].job_name
        if raw_value == "0":
            return None
        try:
            selected = int(raw_value)
        except ValueError:
            output_func("Enter one of the displayed option numbers.")
            continue
        if 1 <= selected <= len(definitions):
            return definitions[selected - 1].job_name
        if selected == manual_option:
            return _prompt_manual_cube_refresh_job_name(
                default_name=default_name,
                input_func=input_func,
                output_func=output_func,
            )
        output_func("Enter one of the displayed option numbers.")


def _prompt_manual_cube_refresh_job_name(
    *,
    default_name: str | None,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> str:
    """Prompt until an exact non-empty saved Cube Refresh name is entered."""
    while True:
        default_hint = f" [{default_name}]" if default_name else ""
        value = input_func(
            f"Exact saved Cube Refresh job name{default_hint}: "
        ).strip()
        resolved = value or default_name
        if resolved:
            return resolved
        output_func("A saved Cube Refresh job name is required.")


def _run_pipeline_command(
    arguments: argparse.Namespace,
    *,
    settings: Settings,
    interactive: bool,
    input_func: InputFunction,
    output_func: OutputFunction,
    logger: logging.Logger,
) -> int:
    """Resolve, execute, and report one Data Integration Pipeline."""
    execution_engine = (
        arguments.metadata_engine or settings.default_pipeline_engine
    )
    setattr(arguments, "_resolved_engine", execution_engine)
    pipeline_code = str(
        arguments.pipeline_code or settings.default_pipeline_code or ""
    ).strip()
    variables = _parse_pipeline_variable_arguments(
        arguments.pipeline_variables
    )
    pipeline_uploads = _parse_named_pipeline_files(
        arguments.pipeline_uploads,
        option_name="--pipeline-upload",
    )
    pipeline_inbox_files = _parse_named_pipeline_files(
        arguments.pipeline_inbox_files,
        option_name="--pipeline-inbox",
    )
    duplicate_file_inputs = (
        set(pipeline_uploads) & set(pipeline_inbox_files)
    )
    if duplicate_file_inputs:
        raise ConfigurationError(
            "Pipeline input(s) cannot use both --pipeline-upload and "
            f"--pipeline-inbox: {', '.join(sorted(duplicate_file_inputs))}."
        )

    if interactive or execution_engine == "rest":
        with EPMClient(
            settings,
            logger=logger.getChild("pipeline_client"),
        ) as client:
            client.authenticate()
            output_func("Successfully connected to Oracle Planning.")
            service = PipelineService(
                client,
                logger=logger.getChild("pipeline_service"),
            )
            if interactive and not getattr(
                arguments,
                "_cycle_preconfigured",
                False,
            ):
                selected_code = _select_pipeline_code(
                    default_code=pipeline_code or None,
                    catalog_file=settings.pipeline_catalog_file,
                    input_func=input_func,
                    output_func=output_func,
                )
                if selected_code is None:
                    setattr(arguments, "_task_cancelled", True)
                    output_func("Pipeline execution cancelled.")
                    return 0
                pipeline_code = selected_code

            if not pipeline_code:
                raise ConfigurationError(
                    "Provide --pipeline or set DEFAULT_PIPELINE_CODE."
                )

            details = service.get_pipeline_details(pipeline_code)
            preflight = PipelinePreflightService(
                logger=logger.getChild("pipeline_preflight"),
            )
            requirements = preflight.discover_file_requirements(details)
            if interactive:
                variables = _prompt_pipeline_variables(
                    details,
                    initial_values=variables,
                    file_variable_names={
                        requirement.variable_name.casefold()
                        for requirement in requirements
                        if requirement.variable_name
                    },
                    input_func=input_func,
                    output_func=output_func,
                )
                selections = _prompt_pipeline_file_selections(
                    requirements,
                    input_func=input_func,
                    output_func=output_func,
                )
                if selections is None:
                    setattr(arguments, "_task_cancelled", True)
                    output_func("Pipeline execution cancelled.")
                    return 0
                variables = _apply_pipeline_file_variables(
                    variables,
                    selections,
                )
                variables = _resolve_pipeline_variables(
                    details,
                    variables,
                    include_defaults=False,
                )
                if not _confirm_pipeline(
                    details,
                    variables,
                    file_selections=selections,
                    engine=execution_engine,
                    input_func=input_func,
                    output_func=output_func,
                ):
                    setattr(arguments, "_task_cancelled", True)
                    output_func("Pipeline execution cancelled.")
                    return 0
            else:
                selections = _resolve_noninteractive_pipeline_files(
                    requirements,
                    supplied_variables=variables,
                    local_uploads=pipeline_uploads,
                    inbox_files=pipeline_inbox_files,
                )
                variables = _apply_pipeline_file_variables(
                    variables,
                    selections,
                )
                variables = _resolve_pipeline_variables(
                    details,
                    variables,
                    include_defaults=False,
                )

            preflight.stage_uploads(client, selections)
            arguments.pipeline_code = details.code
            arguments.pipeline_variables = [
                f"{name}={value}" for name, value in variables.items()
            ]
            setattr(
                arguments,
                "_resolved_pipeline_files",
                tuple(
                    selection.oracle_reference
                    for selection in selections
                ),
            )
            variables_by_name = {
                name.upper(): value for name, value in variables.items()
            }
            start_period = variables_by_name.get("STARTPERIOD")
            end_period = variables_by_name.get("ENDPERIOD")
            if start_period:
                setattr(
                    arguments,
                    "_resolved_period_name",
                    (
                        start_period
                        if not end_period or end_period == start_period
                        else f"{start_period} to {end_period}"
                    ),
                )

            if execution_engine == "rest":
                _execute_rest_pipeline(
                    service,
                    settings,
                    pipeline_code=details.code,
                    variables=variables,
                    logger=logger,
                    output_func=output_func,
                )
                return 0

    if execution_engine == "epmautomate":
        if not interactive and (
            pipeline_uploads or pipeline_inbox_files
        ):
            raise ConfigurationError(
                "Non-interactive EPM Automate pipeline execution cannot "
                "discover file inputs. Stage files first or use the REST "
                "engine with --pipeline-upload/--pipeline-inbox."
            )
        if not pipeline_code:
            raise ConfigurationError(
                "Provide --pipeline or set DEFAULT_PIPELINE_CODE."
            )
        arguments.pipeline_code = pipeline_code
        _execute_epm_automate_pipeline(
            settings,
            pipeline_code=pipeline_code,
            variables=variables,
            logger=logger,
            output_func=output_func,
        )
        return 0

    raise ConfigurationError(
        f"Unsupported Pipeline engine '{execution_engine}'."
    )


def _select_pipeline_code(
    *,
    default_code: str | None,
    catalog_file: Path,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> str | None:
    """Display configured pipelines and return a selected Oracle code."""
    pipelines = list(PipelineCatalogService().load(catalog_file))
    default_index = _find_pipeline_index(pipelines, default_code)
    if default_code and default_index is None:
        pipelines.append(
            PipelineCatalogDefinition(
                code=default_code,
                name="Configured default",
            )
        )
        default_index = len(pipelines)

    manual_option = len(pipelines) + 1
    output_func("\nAvailable Data Integration Pipelines\n")
    for index, definition in enumerate(pipelines, start=1):
        output_func(f"{index}. {definition.display_label}")
    output_func(f"{manual_option}. Enter another pipeline code")
    output_func("0. Cancel")

    while True:
        default_hint = (
            f" [{default_index}]" if default_index is not None else ""
        )
        selection = input_func(
            f"Select a pipeline{default_hint}: "
        ).strip()
        if not selection and default_index is not None:
            return pipelines[default_index - 1].code
        try:
            option = int(selection)
        except ValueError:
            option = -1

        if option == 0:
            return None
        if 1 <= option <= len(pipelines):
            return pipelines[option - 1].code
        if option == manual_option:
            return _prompt_manual_pipeline_code(
                input_func=input_func,
                output_func=output_func,
            )
        output_func(
            f"Invalid selection. Enter a number from 0 to {manual_option}."
        )


def _find_pipeline_index(
    pipelines: Sequence[PipelineCatalogDefinition],
    default_code: str | None,
) -> int | None:
    if not default_code:
        return None
    normalized_default = default_code.casefold()
    for index, definition in enumerate(pipelines, start=1):
        if definition.code.casefold() == normalized_default:
            return index
    return None


def _prompt_manual_pipeline_code(
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> str:
    """Prompt until a valid Oracle pipeline code is supplied."""
    while True:
        value = input_func("Enter the exact pipeline code: ").strip()
        try:
            return PipelineService.validate_pipeline_code(value)
        except EPMError as exc:
            output_func(f"Invalid pipeline code: {exc}")


def _prompt_pipeline_variables(
    details: PipelineDetails,
    *,
    initial_values: Mapping[str, str] | None = None,
    file_variable_names: set[str] | None = None,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> dict[str, str]:
    """Prompt dynamically using the variables returned by Oracle."""
    output_func(
        f"\nPipeline variables for {details.display_name} "
        f"({details.code})\n"
        "Press Enter to accept a displayed default. Optional blank "
        "variables are omitted."
    )
    resolved: dict[str, str] = {}
    supplied_defaults = {
        name.casefold(): value
        for name, value in (initial_values or {}).items()
    }
    resolved_file_variables = file_variable_names or set()
    for variable in details.variables:
        if (
            (variable.variable_type or "").casefold() == "file"
            or variable.name.casefold() in resolved_file_variables
        ):
            continue
        while True:
            selected_default = supplied_defaults.get(
                variable.name.casefold(),
                variable.default_value,
            )
            default_hint = (
                f" [{selected_default}]"
                if selected_default is not None
                else ""
            )
            raw_value = input_func(
                f"{variable.display_name} ({variable.name})"
                f"{default_hint}: "
            ).strip()
            value = (
                raw_value
                if raw_value
                else selected_default
            )
            if value is None:
                if variable.requires_value:
                    output_func(
                        f"{variable.display_name} is required by Oracle."
                    )
                    continue
                break
            try:
                name, normalized_value = (
                    PipelineService.normalize_variable(
                        variable.name,
                        value,
                    )
                )
            except EPMError as exc:
                output_func(f"Invalid value: {exc}")
                continue
            resolved[name] = normalized_value
            break

    normalized_by_name = {
        name.upper(): value for name, value in resolved.items()
    }
    send_mail = normalized_by_name.get("SEND_MAIL", "No")
    if (
        send_mail.casefold() != "no"
        and not normalized_by_name.get("SEND_TO")
    ):
        send_to_variable = next(
            (
                variable
                for variable in details.variables
                if variable.name.upper() == "SEND_TO"
            ),
            None,
        )
        if send_to_variable is None:
            raise ConfigurationError(
                "Oracle native pipeline email is enabled, but this pipeline "
                "does not expose the SEND_TO variable."
            )
        while True:
            recipients = input_func(
                "Send To is required because native pipeline email is "
                "enabled: "
            ).strip()
            if recipients:
                resolved[send_to_variable.name] = recipients
                break
            output_func("Enter at least one email address.")

    return dict(PipelineService.normalize_variables(resolved))


def _parse_pipeline_variable_arguments(
    values: Sequence[str],
) -> dict[str, str]:
    """Parse repeated pipeline NAME=VALUE arguments."""
    variables: dict[str, str] = {}
    seen: set[str] = set()
    for raw_value in values:
        if "=" not in raw_value:
            raise ConfigurationError(
                f"Pipeline variable '{raw_value}' must use NAME=VALUE."
            )
        name, value = raw_value.split("=", 1)
        normalized_name = name.strip()
        normalized_value = value.strip()
        if not normalized_name:
            raise ConfigurationError(
                "Pipeline variable name cannot be empty."
            )
        if not normalized_value:
            raise ConfigurationError(
                f"Pipeline variable '{normalized_name}' requires a value."
            )
        key = normalized_name.casefold()
        if key in seen:
            raise ConfigurationError(
                f"Pipeline variable '{normalized_name}' was provided more "
                "than once."
            )
        seen.add(key)
        variables[normalized_name] = normalized_value
    return dict(PipelineService.normalize_variables(variables))


def _parse_named_pipeline_files(
    values: Sequence[str],
    *,
    option_name: str,
) -> dict[str, str]:
    """Parse repeated INPUT=VALUE pipeline file selections."""
    resolved: dict[str, str] = {}
    seen: set[str] = set()
    for raw_value in values:
        if "=" not in raw_value:
            raise ConfigurationError(
                f"{option_name} value '{raw_value}' must use INPUT=VALUE."
            )
        raw_key, raw_selection = raw_value.split("=", 1)
        key = raw_key.strip()
        selection = raw_selection.strip()
        if not key:
            raise ConfigurationError(
                f"{option_name} input name cannot be empty."
            )
        if not selection:
            raise ConfigurationError(
                f"{option_name} input '{key}' requires a value."
            )
        normalized_key = key.casefold()
        if normalized_key in seen:
            raise ConfigurationError(
                f"{option_name} input '{key}' was provided more than once."
            )
        seen.add(normalized_key)
        resolved[normalized_key] = selection
    return resolved


def _prompt_pipeline_file_selections(
    requirements: Sequence[PipelineFileRequirement],
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> tuple[PipelineFileSelection, ...] | None:
    """Resolve zero or more discovered external pipeline inputs."""
    if not requirements:
        output_func(
            "\nNo external Inbox file inputs were detected for this pipeline."
        )
        return ()

    output_func(
        f"\nExternal pipeline file inputs detected: {len(requirements)}"
    )
    selections: list[PipelineFileSelection] = []
    for index, requirement in enumerate(requirements, start=1):
        consumer_summary = (
            ", ".join(
                consumer.display_label
                for consumer in requirement.consumers
            )
            or "Pipeline variable"
        )
        configured = requirement.configured_reference or "Not configured"
        while True:
            optional_line = (
                "3. Skip this optional input\n"
                if not requirement.required
                else ""
            )
            output_func(
                f"\nInput {index}: {requirement.display_name}\n"
                f"Key: {requirement.key}\n"
                f"Used by: {consumer_summary}\n"
                f"Configured Oracle reference: {configured}\n"
                "\n"
                "1. Upload a local file\n"
                "2. Use a file already in the Oracle Inbox\n"
                f"{optional_line}"
                "0. Cancel pipeline execution"
            )
            choice = input_func("Select a file source: ").strip()
            if choice == "0":
                return None
            if choice == "3" and not requirement.required:
                break
            if choice == "1":
                selection = _prompt_local_pipeline_file(
                    requirement,
                    input_func=input_func,
                    output_func=output_func,
                )
                if selection is not None:
                    selections.append(selection)
                    break
                continue
            if choice == "2":
                selection = _prompt_existing_pipeline_file(
                    requirement,
                    input_func=input_func,
                    output_func=output_func,
                )
                if selection is not None:
                    selections.append(selection)
                    break
                continue
            valid_options = "0, 1, 2, or 3" if not requirement.required else (
                "0, 1, or 2"
            )
            output_func(f"Invalid selection. Enter {valid_options}.")
    return tuple(selections)


def _prompt_local_pipeline_file(
    requirement: PipelineFileRequirement,
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> PipelineFileSelection | None:
    """Prompt for one local pipeline input without uploading it yet."""
    raw_path = input_func(
        "Drag and drop the local input file here, or enter its path "
        "(press Enter to go back): "
    )
    normalized_path = _normalize_dragged_path(raw_path)
    if not normalized_path:
        return None
    path = Path(normalized_path)
    try:
        oracle_reference = local_upload_oracle_reference(
            requirement,
            path,
        )
        return build_pipeline_file_selection(
            requirement,
            source=PipelineFileSource.LOCAL_UPLOAD,
            oracle_reference=oracle_reference,
            local_path=path,
        )
    except ConfigurationError as exc:
        output_func(f"Invalid pipeline input: {exc}")
        return None


def _prompt_existing_pipeline_file(
    requirement: PipelineFileRequirement,
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> PipelineFileSelection | None:
    """Prompt for an existing Inbox reference."""
    default_hint = (
        f" [{requirement.configured_reference}]"
        if requirement.configured_reference
        else ""
    )
    value = input_func(
        f"Oracle Inbox file reference{default_hint} "
        "(press Enter to use the default/go back): "
    ).strip()
    reference = value or requirement.configured_reference
    if not reference:
        return None
    if (
        requirement.variable_name is None
        and requirement.configured_reference
        and reference.casefold()
        != requirement.configured_reference.casefold()
    ):
        output_func(
            "This pipeline job uses a fixed filename. Select its configured "
            "reference or upload a local file that will replace it."
        )
        return None
    return build_pipeline_file_selection(
        requirement,
        source=PipelineFileSource.EXISTING_INBOX,
        oracle_reference=reference,
    )


def _resolve_noninteractive_pipeline_files(
    requirements: Sequence[PipelineFileRequirement],
    *,
    supplied_variables: Mapping[str, str],
    local_uploads: Mapping[str, str],
    inbox_files: Mapping[str, str],
) -> tuple[PipelineFileSelection, ...]:
    """Resolve discovered file requirements from CLI options and defaults."""
    supplied_by_name = {
        name.casefold(): value for name, value in supplied_variables.items()
    }
    remaining_uploads = dict(local_uploads)
    remaining_inbox = dict(inbox_files)
    selections: list[PipelineFileSelection] = []

    for requirement in requirements:
        key = requirement.key.casefold()
        if key in remaining_uploads:
            path = Path(remaining_uploads.pop(key))
            oracle_reference = local_upload_oracle_reference(
                requirement,
                path,
            )
            selections.append(
                build_pipeline_file_selection(
                    requirement,
                    source=PipelineFileSource.LOCAL_UPLOAD,
                    oracle_reference=oracle_reference,
                    local_path=path,
                )
            )
            continue

        if key in remaining_inbox:
            reference = remaining_inbox.pop(key)
            if (
                requirement.variable_name is None
                and requirement.configured_reference
                and reference.casefold()
                != requirement.configured_reference.casefold()
            ):
                raise ConfigurationError(
                    f"Pipeline input '{requirement.key}' is fixed to "
                    f"'{requirement.configured_reference}' and cannot use "
                    f"'{reference}'."
                )
            selections.append(
                build_pipeline_file_selection(
                    requirement,
                    source=PipelineFileSource.EXISTING_INBOX,
                    oracle_reference=reference,
                )
            )
            continue

        if (
            requirement.variable_name
            and requirement.variable_name.casefold() in supplied_by_name
        ):
            selections.append(
                build_pipeline_file_selection(
                    requirement,
                    source=PipelineFileSource.EXISTING_INBOX,
                    oracle_reference=supplied_by_name[
                        requirement.variable_name.casefold()
                    ],
                )
            )
            continue

        if requirement.configured_reference:
            selections.append(
                build_pipeline_file_selection(
                    requirement,
                    source=PipelineFileSource.EXISTING_INBOX,
                    oracle_reference=requirement.configured_reference,
                )
            )
            continue

        if requirement.required:
            raise ConfigurationError(
                f"Pipeline input '{requirement.key}' requires either "
                f"--pipeline-upload \"{requirement.key}=LOCAL_FILE\" or "
                f"--pipeline-inbox \"{requirement.key}=ORACLE_FILE\"."
            )

    unused = sorted(
        set(remaining_uploads) | set(remaining_inbox)
    )
    if unused:
        raise ConfigurationError(
            "File option(s) do not match a discovered pipeline input: "
            + ", ".join(unused)
        )
    return tuple(selections)


def _apply_pipeline_file_variables(
    variables: Mapping[str, str],
    selections: Sequence[PipelineFileSelection],
) -> dict[str, str]:
    """Bind selected file references to their Oracle FILE variables."""
    resolved = dict(variables)
    for selection in selections:
        variable_name = selection.requirement.variable_name
        if variable_name:
            resolved[variable_name] = selection.oracle_reference
    return resolved


def _resolve_pipeline_variables(
    details: PipelineDetails,
    supplied: Mapping[str, str],
    *,
    include_defaults: bool,
) -> dict[str, str]:
    """Canonicalize supplied names and validate required variables."""
    definitions = {
        variable.name.casefold(): variable
        for variable in details.variables
    }
    unknown = [
        name for name in supplied if name.casefold() not in definitions
    ]
    if unknown:
        raise ConfigurationError(
            f"Pipeline '{details.code}' does not define variable(s): "
            f"{', '.join(unknown)}."
        )

    supplied_by_key = {
        name.casefold(): value for name, value in supplied.items()
    }
    resolved: dict[str, str] = {}
    for variable in details.variables:
        key = variable.name.casefold()
        if key in supplied_by_key:
            resolved[variable.name] = supplied_by_key[key]
        elif include_defaults and variable.default_value is not None:
            resolved[variable.name] = variable.default_value
        elif variable.requires_value and variable.default_value is None:
            raise ConfigurationError(
                f"Pipeline '{details.code}' requires variable "
                f"{variable.name}. Supply --variable "
                f"\"{variable.name}=VALUE\"."
            )
    return dict(PipelineService.normalize_variables(resolved))


def _confirm_pipeline(
    details: PipelineDetails,
    variables: Mapping[str, str],
    *,
    file_selections: Sequence[PipelineFileSelection] = (),
    engine: str,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> bool:
    """Display the exact pipeline request before execution."""
    variable_summary = (
        "\n".join(
            f"  {name} = {value}" for name, value in variables.items()
        )
        if variables
        else "  Oracle pipeline defaults"
    )
    file_summary = (
        "\n".join(
            (
                f"  {selection.requirement.display_name}: "
                f"{selection.oracle_reference} "
                f"({'upload ' + str(selection.local_path) if selection.requires_upload else 'existing Inbox file'})"
            )
            for selection in file_selections
        )
        if file_selections
        else "  None"
    )
    output_func(
        "\nPipeline execution summary\n"
        f"Engine: {engine}\n"
        f"Pipeline: {details.display_name} ({details.code})\n"
        f"Stages: {len(details.stages)}\n"
        f"Jobs: {details.job_count}\n"
        "External file inputs:\n"
        f"{file_summary}\n"
        "Variables:\n"
        f"{variable_summary}"
    )
    confirmation = input_func("Continue? [y/N]: ").strip().casefold()
    return confirmation in {"y", "yes"}


def _execute_rest_pipeline(
    service: PipelineService,
    settings: Settings,
    *,
    pipeline_code: str,
    variables: Mapping[str, str],
    logger: logging.Logger,
    output_func: OutputFunction,
) -> PipelineSubmission:
    """Submit and monitor one pipeline through the REST API."""
    started_at = time.monotonic()
    monitor = JobMonitor(
        service,
        poll_interval=settings.default_poll_interval,
        timeout=settings.default_job_timeout,
        logger=logger.getChild("pipeline_monitor"),
    )
    submission = service.start_pipeline(
        pipeline_code,
        variables=variables,
    )
    final_job = monitor.wait_for_completion(submission.job_id)
    statistics = _statistics_from_job_result(final_job, logger=logger)
    elapsed = time.monotonic() - started_at
    output_func(
        "Pipeline completed successfully using REST. "
        f"Job ID: {final_job.job_id}; "
        f"Pipeline: {submission.pipeline_code}; "
        f"Execution time: {elapsed:.2f} seconds."
        f"{_record_statistics_summary(statistics)}"
    )
    return submission


def _execute_epm_automate_pipeline(
    settings: Settings,
    *,
    pipeline_code: str,
    variables: Mapping[str, str],
    logger: logging.Logger,
    output_func: OutputFunction,
) -> None:
    """Execute one pipeline through EPM Automate."""
    started_at = time.monotonic()
    password_file = settings.require_epm_automate_password_file()
    runner = EPMAutomateRunner(
        settings.epm_automate_executable,
        timeout=settings.epm_automate_command_timeout,
        logger=logger.getChild("epm_automate_runner"),
    )
    service = EPMAutomatePipelineService(
        runner,
        username=settings.epm_username,
        password_file=password_file,
        base_url=settings.epm_base_url,
        logger=logger.getChild("epm_automate_pipeline_service"),
    )
    result = service.run_pipeline(
        pipeline_code,
        variables=variables,
    )
    elapsed = time.monotonic() - started_at
    output_func(
        "Pipeline completed successfully using EPM Automate. "
        f"Pipeline: {result.pipeline_code}; "
        f"Execution time: {elapsed:.2f} seconds."
    )


def _run_data_map_command(
    arguments: argparse.Namespace,
    *,
    settings: Settings,
    interactive: bool,
    input_func: InputFunction,
    output_func: OutputFunction,
    logger: logging.Logger,
) -> int:
    """Resolve, execute, and optionally validate one Planning Data Map."""
    engine = (
        arguments.metadata_engine or settings.default_data_map_engine
    )
    setattr(arguments, "_resolved_engine", engine)
    data_map_name = str(
        arguments.data_map_name or settings.default_data_map_name or ""
    ).strip()
    overrides = _parse_name_value_arguments(
        arguments.data_map_overrides,
        option_name="--map-override",
    )
    exclusions = _parse_name_value_arguments(
        arguments.data_map_exclusions,
        option_name="--map-exclude",
    )

    if interactive or engine == "rest":
        with EPMClient(
            settings,
            logger=logger.getChild("data_map_client"),
        ) as client:
            client.authenticate()
            output_func("Successfully connected to Oracle Planning.")
            if interactive:
                selected = _select_data_map_name(
                    JobService(
                        client,
                        logger=logger.getChild("job_service"),
                    ),
                    default_name=data_map_name or None,
                    input_func=input_func,
                    output_func=output_func,
                )
                if selected is None:
                    setattr(arguments, "_task_cancelled", True)
                    output_func("Data Map execution cancelled.")
                    return 0
                data_map_name = selected
                if arguments.clear_target is None:
                    arguments.clear_target = _prompt_yes_no(
                        "Clear the target region before copying data?",
                        default=False,
                        input_func=input_func,
                    )
                if not _confirm_data_map(
                    data_map_name,
                    clear_target=bool(arguments.clear_target),
                    overrides=overrides,
                    exclusions=exclusions,
                    engine=engine,
                    input_func=input_func,
                    output_func=output_func,
                ):
                    setattr(arguments, "_task_cancelled", True)
                    output_func("Data Map execution cancelled.")
                    return 0
            arguments.data_map_name = data_map_name
            if engine == "rest":
                service = DataMapService(
                    client,
                    logger=logger.getChild("data_map_service"),
                )
                _execute_rest_data_map(
                    service,
                    settings,
                    data_map_name=data_map_name,
                    clear_target=bool(arguments.clear_target),
                    overrides=overrides,
                    exclusions=exclusions,
                    logger=logger,
                    output_func=output_func,
                )

    if engine == "epmautomate":
        if overrides or exclusions:
            raise ConfigurationError(
                "Data Map member overrides and exclusions require the REST "
                "engine."
            )
        if not data_map_name:
            raise ConfigurationError(
                "Provide --data-map or set DEFAULT_DATA_MAP_NAME."
            )
        arguments.data_map_name = data_map_name
        _execute_epm_automate_data_map(
            settings,
            data_map_name=data_map_name,
            clear_target=bool(arguments.clear_target),
            logger=logger,
            output_func=output_func,
        )

    if engine not in {"rest", "epmautomate"}:
        raise ConfigurationError(
            f"Unsupported Data Map engine '{engine}'."
        )

    if arguments.command == "data-map":
        source_form = arguments.source_form or settings.validation_source_form
        target_form = arguments.target_form or settings.validation_target_form
        if source_form and target_form:
            _execute_data_validation(
                settings,
                source_form=source_form,
                target_form=target_form,
                tolerance=(
                    arguments.validation_tolerance
                    if arguments.validation_tolerance is not None
                    else settings.validation_tolerance
                ),
                logger=logger,
                output_func=output_func,
            )
    return 0


def _run_monthly_forecast_workflow(
    arguments: argparse.Namespace,
    *,
    settings: Settings,
    interactive: bool,
    input_func: InputFunction,
    output_func: OutputFunction,
    logger: logging.Logger,
) -> int:
    """Run calculation/loading, optional publishing, and validation."""
    setattr(arguments, "_resolved_engine", "rest")
    process_definition = getattr(
        arguments,
        "_planning_process_definition",
        None,
    )
    if process_definition is not None and not isinstance(
        process_definition,
        PlanningProcessDefinition,
    ):
        raise ConfigurationError(
            "The resolved Planning process definition is invalid."
        )
    arguments.pipeline_code = (
        arguments.pipeline_code or settings.default_pipeline_code
    )
    configured_data_map_step = _find_process_step(
        process_definition,
        PlanningProcessStepType.RUN_DATA_MAP,
    )
    run_data_map = (
        not arguments.skip_data_map
        and (
            (
                configured_data_map_step is not None
                and configured_data_map_step.enabled_by_default
            )
            if process_definition is not None
            else (
                bool(arguments.data_map_name)
                or settings.monthly_forecast_run_data_map
            )
        )
    )
    if interactive and (
        process_definition is None or configured_data_map_step is not None
    ):
        run_data_map = _prompt_yes_no(
            "Run the Data Map after the pipeline?",
            default=settings.monthly_forecast_run_data_map,
            input_func=input_func,
        )

    if run_data_map:
        arguments.data_map_name = (
            arguments.data_map_name or settings.default_data_map_name
        )
        if interactive and not getattr(
            arguments,
            "_cycle_preconfigured",
            False,
        ):
            with EPMClient(
                settings,
                logger=logger.getChild("workflow_discovery_client"),
            ) as client:
                client.authenticate()
                selected = _select_data_map_name(
                    JobService(client),
                    default_name=arguments.data_map_name,
                    input_func=input_func,
                    output_func=output_func,
                )
            if selected is None:
                setattr(arguments, "_task_cancelled", True)
                output_func("Monthly Forecast workflow cancelled.")
                return 0
            arguments.data_map_name = selected
        elif not arguments.data_map_name:
            raise ConfigurationError(
                "The workflow requires --data-map or "
                "DEFAULT_DATA_MAP_NAME unless --skip-data-map is used."
            )
        if interactive and arguments.clear_target is None:
            arguments.clear_target = _prompt_yes_no(
                "Clear the Data Map target region?",
                default=settings.monthly_forecast_clear_target,
                input_func=input_func,
            )
    if arguments.clear_target is None:
        arguments.clear_target = settings.monthly_forecast_clear_target

    source_form = arguments.source_form or settings.validation_source_form
    target_form = arguments.target_form or settings.validation_target_form
    validation_enabled = bool(
        run_data_map and source_form and target_form
    )
    if run_data_map and not validation_enabled:
        output_func(
            "Automatic source-target validation is not configured. "
            "Set VALIDATION_SOURCE_FORM and VALIDATION_TARGET_FORM after "
            "creating matching validation forms in Planning."
        )

    refresh_step_definition = _find_process_step(
        process_definition,
        PlanningProcessStepType.REFRESH_CUBE,
    )
    report_step_definition = _find_process_step(
        process_definition,
        PlanningProcessStepType.GENERATE_REPORT,
    )
    run_refresh = bool(
        refresh_step_definition
        and refresh_step_definition.enabled_by_default
    )
    if getattr(arguments, "process_run_refresh", None) is not None:
        run_refresh = bool(arguments.process_run_refresh)
    if interactive and refresh_step_definition is not None:
        run_refresh = _prompt_yes_no(
            "Refresh the Planning cube before running the pipeline?",
            default=run_refresh,
            input_func=input_func,
        )

    run_report = bool(
        report_step_definition
        and report_step_definition.enabled_by_default
        and not getattr(arguments, "skip_report", False)
    )
    if interactive and report_step_definition is not None:
        run_report = _prompt_yes_no(
            "Generate the configured Planning report after validation?",
            default=run_report,
            input_func=input_func,
        )

    if process_definition is not None and interactive:
        output_func(
            "\nEnd-to-End Planning Process Summary\n"
            f"Process: {process_definition.display_name} "
            f"({process_definition.code})\n"
            f"Year: {arguments.year or 'Not supplied'}\n"
            f"Period: {arguments.start_period or 'Not supplied'} to "
            f"{arguments.end_period or 'Not supplied'}\n"
            f"Scenario: {arguments.scenario or 'Not supplied'}\n"
            f"Version: {arguments.cycle_version or 'Not supplied'}\n"
            f"Pipeline: {arguments.pipeline_code}\n"
            f"Data Map: "
            f"{arguments.data_map_name if run_data_map else 'Skipped'}\n"
            f"Clear Data Map target: "
            f"{'Yes' if run_data_map and arguments.clear_target else 'No'}\n"
            f"Cube Refresh: "
            f"{'Yes' if run_refresh else 'Skipped'}\n"
            f"Source-target validation: "
            f"{'Yes' if validation_enabled else 'Skipped'}\n"
            f"Planning report: "
            f"{'Yes' if run_report else 'Skipped'}"
        )
        if not _prompt_yes_no(
            "Approve this end-to-end Planning Process?",
            default=False,
            input_func=input_func,
        ):
            setattr(arguments, "_task_cancelled", True)
            output_func(
                "Planning Process cancelled before making changes."
            )
            return 0

    repository = SQLWorkflowRepository(
        settings.database_target
    )
    engine = WorkflowEngine(
        repository,
        logger=logger.getChild("workflow_engine"),
    )
    cycle_updates = tuple(
        getattr(arguments, "_cycle_variable_updates", ())
    )
    if (
        isinstance(process_definition, PlanningProcessDefinition)
        and cycle_updates
    ):
        process_definition = _with_variable_update_step(
            process_definition
        )

    def update_cycle_variables_step() -> Mapping[str, object]:
        with EPMClient(
            settings,
            logger=logger.getChild("cycle_variable_client"),
        ) as client:
            client.authenticate()
            changed = SubstitutionVariableService(
                client,
                logger=logger.getChild("substitution_variable_service"),
            ).apply_updates(cycle_updates)
        output_func(
            f"Planning cycle variables updated: {len(changed)} changed."
        )
        return {
            "changes": [
                {
                    "scope": update.scope,
                    "name": update.name,
                    "old_value": update.old_value,
                    "new_value": update.new_value,
                }
                for update in changed
            ]
        }

    def run_pipeline_step() -> Mapping[str, object]:
        exit_code = _run_pipeline_command(
            arguments,
            settings=settings,
            interactive=interactive,
            input_func=input_func,
            output_func=output_func,
            logger=logger,
        )
        if exit_code != 0 or getattr(arguments, "_task_cancelled", False):
            raise ConfigurationError(
                "Pipeline execution did not complete."
            )
        return {"pipeline_code": arguments.pipeline_code}

    def run_data_map_step() -> Mapping[str, object]:
        original_command = arguments.command
        arguments.command = "workflow"
        try:
            _run_data_map_command(
                arguments,
                settings=settings,
                interactive=False,
                input_func=input_func,
                output_func=output_func,
                logger=logger,
            )
        finally:
            arguments.command = original_command
        return {
            "data_map_name": arguments.data_map_name,
            "clear_target": bool(arguments.clear_target),
        }

    def run_business_rule_step(
        step: PlanningProcessStepDefinition,
    ) -> Mapping[str, object]:
        rule_name = str(step.parameters.get("ruleName", "")).strip()
        raw_prompts = step.parameters.get("runtimePrompts") or {}
        if not isinstance(raw_prompts, Mapping):
            raise ConfigurationError(
                f"Process Business Rule '{rule_name}' runtimePrompts must "
                "be an object."
            )
        original_command = arguments.command
        original_rule = arguments.rule_name
        original_prompts = list(arguments.runtime_prompts)
        original_engine = arguments.metadata_engine
        try:
            arguments.command = "process"
            arguments.rule_name = rule_name
            arguments.runtime_prompts = [
                f"{name}={value}"
                for name, value in raw_prompts.items()
            ]
            arguments.metadata_engine = "rest"
            _run_business_rule_command(
                arguments,
                settings=settings,
                interactive=False,
                input_func=input_func,
                output_func=output_func,
                logger=logger,
            )
        finally:
            arguments.command = original_command
            arguments.rule_name = original_rule
            arguments.runtime_prompts = original_prompts
            arguments.metadata_engine = original_engine
        return {
            "rule_name": rule_name,
            "runtime_prompts": dict(raw_prompts),
        }

    def validate_step() -> Mapping[str, object]:
        result = _execute_data_validation(
            settings,
            source_form=str(source_form),
            target_form=str(target_form),
            tolerance=(
                arguments.validation_tolerance
                if arguments.validation_tolerance is not None
                else settings.validation_tolerance
            ),
            logger=logger,
            output_func=output_func,
        )
        return {
            "compared_cells": result.compared_cells,
            "matched_cells": result.matched_cells,
        }

    def process_preflight_step(
        step: PlanningProcessStepDefinition,
    ) -> Mapping[str, object]:
        return {
            "process_code": process_definition.code,
            "cycle_code": process_definition.cycle_code,
            "pipeline_code": arguments.pipeline_code,
            "data_map_name": (
                arguments.data_map_name if run_data_map else None
            ),
            "refresh_enabled": run_refresh,
            "validation_enabled": validation_enabled,
            "report_enabled": run_report,
            "report_name": (
                report_step_definition.parameters.get("reportName")
                if report_step_definition is not None
                else None
            ),
        }

    def refresh_cube_step(
        step: PlanningProcessStepDefinition,
    ) -> Mapping[str, object]:
        job_name = str(step.parameters.get("jobName", "")).strip()
        original_job_name = arguments.refresh_job
        try:
            arguments.refresh_job = job_name
            _run_cube_refresh_command(
                arguments,
                settings=settings,
                interactive=False,
                input_func=input_func,
                output_func=output_func,
                logger=logger,
            )
        finally:
            arguments.refresh_job = original_job_name
        return {"job_name": job_name}

    def generate_report_step(
        step: PlanningProcessStepDefinition,
    ) -> Mapping[str, object]:
        report_name = str(
            step.parameters.get("reportName", "")
        ).strip()
        original_command = arguments.command
        original_form = arguments.report_form
        original_title = arguments.report_title
        original_output = arguments.report_output
        original_page_members = list(arguments.report_page_members)
        try:
            arguments.command = "process"
            arguments.report_form = report_name
            arguments.report_title = None
            arguments.report_output = None
            arguments.report_page_members = [
                f"{dimension}={value}"
                for dimension, value in (
                    ("Scenario", arguments.scenario),
                    ("Version", arguments.cycle_version),
                    ("Year", arguments.year),
                )
                if value
            ]
            _run_form_report_command(
                arguments,
                settings=settings,
                interactive=False,
                input_func=input_func,
                output_func=output_func,
                logger=logger,
            )
            generated_output = arguments.report_output
        finally:
            arguments.command = original_command
            arguments.report_form = original_form
            arguments.report_title = original_title
            arguments.report_page_members = original_page_members
        arguments.report_output = generated_output
        return {
            "report_name": report_name,
            "output_path": str(generated_output),
        }

    if process_definition is not None:
        handlers = {
            PlanningProcessStepType.PREFLIGHT: process_preflight_step,
            PlanningProcessStepType.UPDATE_VARIABLES: (
                lambda step: update_cycle_variables_step()
            ),
            PlanningProcessStepType.REFRESH_CUBE: refresh_cube_step,
            PlanningProcessStepType.RUN_PIPELINE: (
                lambda step: run_pipeline_step()
            ),
            PlanningProcessStepType.RUN_BUSINESS_RULE: (
                run_business_rule_step
            ),
            PlanningProcessStepType.RUN_DATA_MAP: (
                lambda step: run_data_map_step()
            ),
            PlanningProcessStepType.VALIDATE_DATA: (
                lambda step: validate_step()
            ),
            PlanningProcessStepType.GENERATE_REPORT: generate_report_step,
        }
        run = PlanningProcessOrchestrator(
            engine,
            logger=logger.getChild("planning_process_orchestrator"),
        ).run(
            process_definition,
            handlers=handlers,
            enabled_overrides={
                PlanningProcessStepType.UPDATE_VARIABLES: bool(
                    cycle_updates
                ),
                PlanningProcessStepType.REFRESH_CUBE: run_refresh,
                PlanningProcessStepType.RUN_DATA_MAP: run_data_map,
                PlanningProcessStepType.VALIDATE_DATA: validation_enabled,
                PlanningProcessStepType.GENERATE_REPORT: run_report,
            },
            execution_id=getattr(
                arguments,
                "_execution_id_override",
                None,
            ),
        )
        output_func(
            f"{process_definition.display_name} completed successfully. "
            f"Execution ID: {run.execution_id}."
        )
        if not run_data_map:
            output_func(
                "Data Map was skipped; the reporting cube was not updated."
            )
        if not run_report:
            output_func("Planning report generation was skipped.")
        return 0

    workflow_steps: list[WorkflowStep] = []
    if cycle_updates:
        workflow_steps.append(
            WorkflowStep(
                "Update Planning Cycle Variables",
                update_cycle_variables_step,
            )
        )
    workflow_steps.extend(
        (
            WorkflowStep("Run Planning Pipeline", run_pipeline_step),
            WorkflowStep(
                "Publish Reporting Data Map",
                run_data_map_step,
                enabled=run_data_map,
                skip_reason=(
                    "Data Map publishing was disabled for this run; the "
                    "reporting cube was not updated."
                ),
            ),
            WorkflowStep(
                "Validate Source and Target",
                validate_step,
                enabled=validation_enabled,
                skip_reason=(
                    "Matching source and target validation forms were not "
                    "configured."
                ),
            ),
        )
    )
    run = engine.run(
        "MONTHLY_FORECAST",
        tuple(workflow_steps),
    )
    output_func(
        "Monthly Forecast workflow completed successfully. "
        f"Execution ID: {run.execution_id}."
    )
    if not run_data_map:
        output_func(
            "Data Map was skipped; the reporting cube was not updated."
        )
    return 0


def _find_process_step(
    definition: PlanningProcessDefinition | None,
    step_type: PlanningProcessStepType,
) -> PlanningProcessStepDefinition | None:
    """Return the first configured process step of the requested type."""
    if definition is None:
        return None
    return next(
        (
            step
            for step in definition.steps
            if step.step_type is step_type
        ),
        None,
    )


def _with_variable_update_step(
    definition: PlanningProcessDefinition,
) -> PlanningProcessDefinition:
    """Add the optional update stage to older process definitions."""
    if any(
        step.step_type is PlanningProcessStepType.UPDATE_VARIABLES
        for step in definition.steps
    ):
        return definition
    steps = list(definition.steps)
    insert_at = next(
        (
            index + 1
            for index, step in enumerate(steps)
            if step.step_type is PlanningProcessStepType.PREFLIGHT
        ),
        0,
    )
    steps.insert(
        insert_at,
        PlanningProcessStepDefinition(
            step_type=PlanningProcessStepType.UPDATE_VARIABLES,
            name="Update Selected Substitution Variables",
            enabled_by_default=False,
        ),
    )
    return PlanningProcessDefinition(
        code=definition.code,
        display_name=definition.display_name,
        cycle_code=definition.cycle_code,
        steps=tuple(steps),
        context_mode=definition.context_mode,
    )


def _is_pipeline_only_process(
    definition: PlanningProcessDefinition | None,
) -> bool:
    """Return whether a process delegates all orchestration to Oracle."""
    preflight = _find_process_step(
        definition,
        PlanningProcessStepType.PREFLIGHT,
    )
    return bool(
        preflight and preflight.parameters.get("pipelineOnly") is True
    )


def _execute_rest_data_map(
    service: DataMapService,
    settings: Settings,
    *,
    data_map_name: str,
    clear_target: bool,
    overrides: Mapping[str, str],
    exclusions: Mapping[str, str],
    logger: logging.Logger,
    output_func: OutputFunction,
) -> DataMapSubmission:
    """Submit and monitor one Planning Data Map through REST."""
    started_at = time.monotonic()
    monitor = JobMonitor(
        service,
        poll_interval=settings.default_poll_interval,
        timeout=settings.default_job_timeout,
        logger=logger.getChild("data_map_monitor"),
    )
    submission = service.start_data_map(
        data_map_name,
        clear_target=clear_target,
        member_overrides=overrides,
        exclusion_overrides=exclusions,
    )
    final_job = monitor.wait_for_completion(submission.job_id)
    output_func(
        "Data Map completed successfully using REST. "
        f"Job ID: {final_job.job_id}; "
        f"Data Map: {submission.request.data_map_name}; "
        f"Execution time: {time.monotonic() - started_at:.2f} seconds."
    )
    return submission


def _execute_epm_automate_data_map(
    settings: Settings,
    *,
    data_map_name: str,
    clear_target: bool,
    logger: logging.Logger,
    output_func: OutputFunction,
) -> None:
    """Execute one Planning Data Map through EPM Automate."""
    started_at = time.monotonic()
    runner = EPMAutomateRunner(
        settings.epm_automate_executable,
        timeout=settings.epm_automate_command_timeout,
        logger=logger.getChild("epm_automate_runner"),
    )
    service = EPMAutomateDataMapService(
        runner,
        username=settings.epm_username,
        password_file=settings.require_epm_automate_password_file(),
        base_url=settings.epm_base_url,
        logger=logger.getChild("epm_automate_data_map_service"),
    )
    result = service.run_data_map(
        data_map_name,
        clear_target=clear_target,
    )
    output_func(
        "Data Map completed successfully using EPM Automate. "
        f"Data Map: {result.request.data_map_name}; "
        f"Execution time: {time.monotonic() - started_at:.2f} seconds."
    )


def _execute_data_validation(
    settings: Settings,
    *,
    source_form: str,
    target_form: str,
    tolerance: float,
    logger: logging.Logger,
    output_func: OutputFunction,
):
    """Compare source and target validation forms and enforce equality."""
    with EPMClient(
        settings,
        logger=logger.getChild("validation_client"),
    ) as client:
        client.authenticate()
        result = DataValidationService(
            client,
            logger=logger.getChild("data_validation_service"),
        ).compare_forms(
            source_form,
            target_form,
            tolerance=Decimal(str(tolerance)),
        )
    if not result.is_successful:
        mismatch_count = result.compared_cells - result.matched_cells
        for mismatch in result.mismatches[:10]:
            output_func(
                "Mismatch: "
                f"row={mismatch.row_headers}, "
                f"column={mismatch.column_headers}, "
                f"source={mismatch.source_value}, "
                f"target={mismatch.target_value}."
            )
        raise DataValidationError(
            f"Source-target validation failed for {mismatch_count} of "
            f"{result.compared_cells} cells."
        )
    output_func(
        "Source-target validation successful. "
        f"Compared cells: {result.compared_cells}."
    )
    return result


def _select_data_map_name(
    job_service: JobService,
    *,
    default_name: str | None,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> str | None:
    """List saved Data Maps and return the selected job definition name."""
    definitions = list(
        job_service.get_job_definitions(job_type="PLAN_TYPE_MAP")
    )
    default_index = next(
        (
            index
            for index, definition in enumerate(definitions, start=1)
            if default_name
            and definition.job_name.casefold() == default_name.casefold()
        ),
        None,
    )
    manual_option = len(definitions) + 1
    output_func("\nAvailable Planning Data Maps")
    for index, definition in enumerate(definitions, start=1):
        output_func(f"{index}. {definition.job_name}")
    output_func(f"{manual_option}. Enter another Data Map name")
    output_func("0. Cancel")
    default_hint = (
        f" [{default_index}]" if default_index is not None else ""
    )
    while True:
        value = input_func(
            f"Select a Data Map{default_hint}: "
        ).strip()
        if not value and default_index is not None:
            return definitions[default_index - 1].job_name
        if value == "0":
            return None
        try:
            option = int(value)
        except ValueError:
            output_func("Enter one of the displayed option numbers.")
            continue
        if 1 <= option <= len(definitions):
            return definitions[option - 1].job_name
        if option == manual_option:
            while True:
                name = input_func(
                    "Enter the exact Data Map name: "
                ).strip()
                if name:
                    return name
                output_func("Data Map name cannot be empty.")
        output_func("Enter one of the displayed option numbers.")


def _confirm_data_map(
    data_map_name: str,
    *,
    clear_target: bool,
    overrides: Mapping[str, str],
    exclusions: Mapping[str, str],
    engine: str,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> bool:
    """Display the exact Data Map request before execution."""
    output_func(
        "\nData Map execution summary\n"
        f"Data Map: {data_map_name}\n"
        f"Engine: {engine}\n"
        f"Clear target: {'Yes' if clear_target else 'No'}\n"
        f"Overrides: {dict(overrides) or 'None'}\n"
        f"Exclusions: {dict(exclusions) or 'None'}"
    )
    return (
        input_func("Continue? [y/N]: ").strip().casefold()
        in {"y", "yes"}
    )


def _prompt_yes_no(
    prompt: str,
    *,
    default: bool,
    input_func: InputFunction,
) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    value = input_func(f"{prompt} {suffix}: ").strip().casefold()
    if not value:
        return default
    return value in {"y", "yes"}


def _parse_name_value_arguments(
    values: Sequence[str],
    *,
    option_name: str,
) -> dict[str, str]:
    """Parse repeated NAME=VALUE arguments with duplicate protection."""
    parsed: dict[str, str] = {}
    normalized_names: set[str] = set()
    for raw_value in values:
        name, separator, value = str(raw_value).partition("=")
        name = name.strip()
        value = value.strip()
        if not separator or not name or not value:
            raise ConfigurationError(
                f"{option_name} requires NAME=VALUE."
            )
        key = name.casefold()
        if key in normalized_names:
            raise ConfigurationError(
                f"{option_name} contains duplicate name '{name}'."
            )
        normalized_names.add(key)
        parsed[name] = value
    return parsed


def _parse_scoped_variable_arguments(
    values: Sequence[str],
    *,
    option_name: str,
) -> dict[tuple[str, str], str]:
    """Parse repeated SCOPE.NAME=VALUE substitution-variable arguments."""
    parsed: dict[tuple[str, str], str] = {}
    seen: set[tuple[str, str]] = set()
    for raw_value in values:
        target, separator, value = str(raw_value).partition("=")
        scope, scope_separator, name = target.strip().partition(".")
        scope = scope.strip()
        name = name.strip()
        value = value.strip()
        if (
            not separator
            or not scope_separator
            or not scope
            or not name
            or not value
        ):
            raise ConfigurationError(
                f"{option_name} requires SCOPE.NAME=VALUE."
            )
        if scope.casefold() == "all":
            scope = "ALL"
        identity = (scope.casefold(), name.casefold())
        if identity in seen:
            raise ConfigurationError(
                f"{option_name} contains duplicate variable "
                f"'{scope}.{name}'."
            )
        seen.add(identity)
        parsed[(scope, name)] = value
    return parsed


def _prompt_integration_name(
    *,
    default_name: str | None,
    catalog_file: Path,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> str:
    """Display configured integrations and return the selected name."""
    integrations = list(
        DataIntegrationCatalogService().load(catalog_file)
    )
    default_index = _find_integration_index(
        integrations,
        default_name,
    )
    if default_name and default_index is None:
        integrations.append(
            DataIntegrationDefinition(
                name=default_name,
                description="Configured default",
            )
        )
        default_index = len(integrations)

    manual_option = len(integrations) + 1
    while True:
        menu_lines = ["\nAvailable Data Integrations", ""]
        menu_lines.extend(
            f"{index}. {definition.display_label}"
            for index, definition in enumerate(integrations, start=1)
        )
        menu_lines.append(
            f"{manual_option}. Enter another integration name"
        )
        output_func("\n".join(menu_lines))

        default_hint = (
            f" [{default_index}]" if default_index is not None else ""
        )
        selection = input_func(
            f"Select an integration{default_hint}: "
        ).strip()
        if not selection and default_index is not None:
            return integrations[default_index - 1].name

        try:
            option = int(selection)
        except ValueError:
            option = -1

        if 1 <= option <= len(integrations):
            return integrations[option - 1].name
        if option == manual_option:
            return _prompt_manual_integration_name(
                default_name=default_name,
                input_func=input_func,
                output_func=output_func,
            )
        output_func(
            f"Invalid selection. Enter a number from 1 to {manual_option}."
        )


def _find_integration_index(
    integrations: Sequence[DataIntegrationDefinition],
    default_name: str | None,
) -> int | None:
    """Return the one-based catalog index matching the configured default."""
    if not default_name:
        return None
    normalized_default = default_name.casefold()
    for index, definition in enumerate(integrations, start=1):
        if definition.name.casefold() == normalized_default:
            return index
    return None


def _prompt_manual_integration_name(
    *,
    default_name: str | None,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> str:
    """Prompt until a non-empty manual integration name is supplied."""
    while True:
        default_hint = f" [{default_name}]" if default_name else ""
        value = input_func(
            f"Data Integration name{default_hint}: "
        ).strip()
        resolved = value or default_name
        if resolved:
            return resolved
        output_func("A Data Integration name is required.")


def _prompt_integration_period_range(
    *,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> DataIntegrationPeriodRange:
    """Prompt for and validate the runtime year and period range."""
    output_func(
        "\nEnter the exact period names configured in Data Integration."
    )
    while True:
        start_period = input_func(
            "Start period (for example Jun-19 or Jun#FY19): "
        ).strip()
        end_period = input_func(
            "End period (press Enter to use the start period): "
        ).strip()
        try:
            return DataIntegrationPeriodRange.from_period_names(
                start_period,
                end_period or start_period,
            )
        except DataIntegrationError as exc:
            output_func(f"Invalid period selection: {exc}")


def _confirm_data_integration(
    arguments: argparse.Namespace,
    *,
    integration_name: str,
    period_range: DataIntegrationPeriodRange,
    import_mode: str,
    export_mode: str,
    engine: str,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> bool:
    """Display the exact column-to-period mapping and ask for confirmation."""
    source = (
        str(arguments.metadata_file)
        if arguments.metadata_file is not None
        else f"Oracle Inbox: {arguments.inbox_file}"
    )
    output_func(
        "\nData Integration load summary\n"
        f"Engine: {engine}\n"
        f"Integration: {integration_name}\n"
        f"File: {source}\n"
        f"Import mode: {import_mode}\n"
        f"Export mode: {export_mode}\n"
        f"Period parameter: {period_range.oracle_period_name}\n"
        "File structure and column mappings: controlled by the Oracle "
        "Data Integration definition."
    )
    confirmation = input_func("Continue? [y/N]: ").strip().casefold()
    return confirmation in {"y", "yes"}


def _execute_rest_data_integration(
    client: EPMClient,
    settings: Settings,
    *,
    data_file: Path | None,
    inbox_file_name: str | None,
    integration_name: str,
    period_range: DataIntegrationPeriodRange,
    import_mode: str,
    export_mode: str,
    logger: logging.Logger,
    output_func: OutputFunction,
) -> None:
    """Upload, submit, and monitor a Data Integration REST job."""
    started_at = time.monotonic()
    if data_file is not None:
        file_service = FileService(
            client,
            allow_any_extension=True,
            logger=logger.getChild("file_service"),
        )
        upload = file_service.upload_to_inbox(data_file)
        file_reference = DataIntegrationFileReference.from_default_upload(
            upload.file_name
        )
    elif inbox_file_name:
        file_reference = DataIntegrationFileReference.from_existing(
            inbox_file_name
        )
        logger.info(
            "Using existing Oracle Data Integration file reference: '%s'.",
            file_reference,
        )
    else:
        raise ConfigurationError(
            "A local Data Integration file or Inbox filename is required."
        )

    service = DataIntegrationService(
        client,
        logger=logger.getChild("data_integration_service"),
    )
    monitor = JobMonitor(
        service,
        poll_interval=settings.default_poll_interval,
        timeout=settings.default_job_timeout,
        logger=logger.getChild("data_integration_monitor"),
    )
    submission = service.start_integration(
        str(file_reference),
        integration_name,
        period_range,
        import_mode=import_mode,
        export_mode=export_mode,
    )
    final_job = monitor.wait_for_completion(submission.job_id)
    statistics = _statistics_from_job_result(final_job, logger=logger)
    elapsed = time.monotonic() - started_at
    output_func(
        "Data Integration completed successfully using REST. "
        f"Job ID: {final_job.job_id}; "
        f"Integration: {submission.integration_name}; "
        f"Periods: {submission.period_name}; "
        f"Execution time: {elapsed:.2f} seconds."
        f"{_record_statistics_summary(statistics)}"
    )


def _execute_epm_automate_data_integration(
    settings: Settings,
    *,
    data_file: Path | None,
    inbox_file_name: str | None,
    integration_name: str,
    period_range: DataIntegrationPeriodRange,
    import_mode: str,
    export_mode: str,
    logger: logging.Logger,
    output_func: OutputFunction,
) -> None:
    """Execute a Data Integration job through EPM Automate."""
    started_at = time.monotonic()
    password_file = settings.require_epm_automate_password_file()
    runner = EPMAutomateRunner(
        settings.epm_automate_executable,
        timeout=settings.epm_automate_command_timeout,
        logger=logger.getChild("epm_automate_runner"),
    )
    service = EPMAutomateDataIntegrationService(
        runner,
        username=settings.epm_username,
        password_file=password_file,
        base_url=settings.epm_base_url,
        logger=logger.getChild("epm_automate_data_integration_service"),
    )
    result = service.run_integration(
        data_file=data_file,
        inbox_file_name=inbox_file_name,
        integration_name=integration_name,
        period_range=period_range,
        import_mode=import_mode,
        export_mode=export_mode,
    )
    elapsed = time.monotonic() - started_at
    output_func(
        "Data Integration completed successfully using EPM Automate. "
        f"Integration: {result.integration_name}; "
        f"File: {result.file_name}; "
        f"Periods: {result.period_name}; "
        f"Replaced existing file: "
        f"{'Yes' if result.replaced_existing else 'No'}; "
        f"Execution time: {elapsed:.2f} seconds."
    )


def _prepare_import_arguments(
    arguments: argparse.Namespace,
    *,
    job_service: JobService,
    job_type: str,
    operation_name: str,
    interactive: bool,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> tuple[str, str | None] | None:
    """Resolve import job options shared by both execution engines."""
    if not interactive:
        return str(arguments.metadata_job_name), arguments.error_file_name

    job_name = _select_import_job(
        job_service,
        job_type=job_type,
        operation_name=operation_name,
        input_func=input_func,
        output_func=output_func,
    )
    if job_name is None:
        output_func(f"{operation_name} load cancelled.")
        return None
    error_file_name = _prompt_error_file_name(input_func)
    if not _confirm_import(
        arguments,
        operation_name=operation_name,
        job_name=job_name,
        error_file_name=error_file_name,
        input_func=input_func,
        output_func=output_func,
    ):
        output_func(f"{operation_name} load cancelled.")
        return None
    return job_name, error_file_name


def _select_import_job(
    job_service: JobService,
    *,
    job_type: str,
    operation_name: str,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> str | None:
    definitions = job_service.get_job_definitions(
        job_type=job_type
    )
    if not definitions:
        raise ConfigurationError(
            f"No saved Import {operation_name} jobs were found in Oracle "
            f"Planning. Create a job in Planning before running the "
            f"{operation_name.lower()} load."
        )

    output_func(f"\nAvailable Import {operation_name} jobs\n")
    for index, definition in enumerate(definitions, start=1):
        output_func(f"{index}. {definition.job_name}")
    output_func("0. Cancel")

    while True:
        raw_selection = input_func("Select a job: ").strip()
        try:
            selection = int(raw_selection)
        except ValueError:
            output_func("Enter the number shown beside the job.")
            continue

        if selection == 0:
            return None
        if 1 <= selection <= len(definitions):
            return definitions[selection - 1].job_name
        output_func(
            f"Enter a number from 0 to {len(definitions)}."
        )


def _prompt_error_file_name(
    input_func: InputFunction,
) -> str | None:
    value = input_func(
        "Optional error ZIP filename (press Enter to skip): "
    ).strip()
    if not value:
        return None
    file_name = PurePath(value).name
    if not file_name.lower().endswith(".zip"):
        file_name += ".zip"
    return file_name


def _confirm_import(
    arguments: argparse.Namespace,
    *,
    operation_name: str,
    job_name: str,
    error_file_name: str | None,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> bool:
    source = (
        str(arguments.metadata_file)
        if arguments.metadata_file is not None
        else f"Oracle Inbox: {arguments.inbox_file}"
    )
    output_func(
        f"\n{operation_name} load summary\n"
        f"Engine: {arguments.metadata_engine or 'rest'}\n"
        f"File: {source}\n"
        f"Job: {job_name}\n"
        f"Error file: {error_file_name or 'Not requested'}"
    )
    confirmation = input_func("Continue? [y/N]: ").strip().lower()
    return confirmation in {"y", "yes"}


def _execute_metadata_load(
    client: EPMClient,
    settings: Settings,
    *,
    job_service: JobService,
    metadata_file: Path | None,
    inbox_file_name: str | None,
    job_name: str,
    import_mode: str,
    error_file_name: str | None,
    logger: logging.Logger,
    output_func: OutputFunction,
) -> None:
    started_at = time.monotonic()

    if metadata_file is not None:
        file_service = FileService(
            client,
            logger=logger.getChild("file_service"),
        )
        upload = file_service.upload_to_inbox(metadata_file)
        file_name = upload.file_name
    elif inbox_file_name:
        file_name = PurePath(inbox_file_name).name
        logger.info(
            "Using existing Oracle Inbox metadata file: '%s'.",
            file_name,
        )
    else:
        raise ConfigurationError(
            "A local metadata file or Inbox filename is required."
        )

    metadata_service = MetadataService(
        client,
        logger=logger.getChild("metadata_service"),
    )
    monitor = JobMonitor(
        job_service,
        poll_interval=settings.default_poll_interval,
        timeout=settings.default_job_timeout,
        logger=logger.getChild("job_monitor"),
    )

    submission = metadata_service.start_import(
        file_name,
        job_name,
        import_mode=import_mode,
        error_file_name=error_file_name,
    )
    final_job = monitor.wait_for_completion(submission.job_id)
    statistics = _planning_job_statistics(
        job_service,
        final_job.job_id,
        logger=logger,
    )
    elapsed = time.monotonic() - started_at

    logger.info(
        "Metadata import successful: job_id=%s, execution_time=%.2fs.",
        final_job.job_id,
        elapsed,
    )
    output_func(
        f"Metadata import completed successfully. "
        f"Job ID: {final_job.job_id}; "
        f"Status: {final_job.descriptive_status or final_job.status}; "
        f"Execution time: {elapsed:.2f} seconds."
        f"{_record_statistics_summary(statistics)}"
    )


def _execute_data_load(
    client: EPMClient,
    settings: Settings,
    *,
    job_service: JobService,
    data_file: Path | None,
    inbox_file_name: str | None,
    job_name: str,
    error_file_name: str | None,
    logger: logging.Logger,
    output_func: OutputFunction,
) -> None:
    """Upload and monitor a native Planning Import Data job."""
    started_at = time.monotonic()

    if data_file is not None:
        file_service = FileService(
            client,
            supported_extensions={".csv", ".txt", ".zip"},
            logger=logger.getChild("file_service"),
        )
        upload = file_service.upload_to_inbox(data_file)
        file_name = upload.file_name
    elif inbox_file_name:
        file_name = PurePath(inbox_file_name).name
        logger.info(
            "Using existing Oracle Inbox data file: '%s'.",
            file_name,
        )
    else:
        raise ConfigurationError(
            "A local data file or Inbox filename is required."
        )

    data_service = DataService(
        client,
        logger=logger.getChild("data_service"),
    )
    monitor = JobMonitor(
        job_service,
        poll_interval=settings.default_poll_interval,
        timeout=settings.default_job_timeout,
        logger=logger.getChild("job_monitor"),
    )
    submission = data_service.start_import(
        file_name,
        job_name,
        error_file_name=error_file_name,
    )
    final_job = monitor.wait_for_completion(submission.job_id)
    statistics = _planning_job_statistics(
        job_service,
        final_job.job_id,
        logger=logger,
    )
    elapsed = time.monotonic() - started_at

    logger.info(
        "Data import successful: job_id=%s, execution_time=%.2fs.",
        final_job.job_id,
        elapsed,
    )
    output_func(
        "Data import completed successfully. "
        f"Job ID: {final_job.job_id}; "
        f"Status: {final_job.descriptive_status or final_job.status}; "
        f"Execution time: {elapsed:.2f} seconds."
        f"{_record_statistics_summary(statistics)}"
    )


def _planning_job_statistics(
    job_service: JobService,
    job_id: int,
    *,
    logger: logging.Logger,
) -> JobRecordStatistics | None:
    """Retrieve optional Planning load counters without failing the job."""
    try:
        return job_service.get_record_statistics(job_id)
    except EPMError as exc:
        logger.warning(
            "Oracle Job Details statistics unavailable for job %s: %s",
            job_id,
            exc,
        )
        return None


def _statistics_from_job_result(
    job: JobResult,
    *,
    logger: logging.Logger,
) -> JobRecordStatistics | None:
    """Read counters included directly in a terminal Oracle response."""
    raw_response = getattr(job, "raw_response", None)
    if not isinstance(raw_response, Mapping):
        return None
    try:
        return JobRecordStatistics.from_response(raw_response)
    except EPMError as exc:
        logger.warning(
            "Ignored invalid Oracle record counters for job %s: %s",
            getattr(job, "job_id", "unknown"),
            exc,
        )
        return None


def _record_statistics_summary(
    statistics: JobRecordStatistics | None,
) -> str:
    """Format official Oracle counters for concise CLI output."""
    if statistics is None:
        return ""
    return (
        "\nRecords read: "
        f"{statistics.records_read}; "
        f"Records processed: {statistics.records_processed}; "
        f"Records rejected: {statistics.records_rejected}."
    )


def _execute_epm_automate_metadata_load(
    settings: Settings,
    *,
    metadata_file: Path | None,
    inbox_file_name: str | None,
    job_name: str,
    error_file_name: str | None,
    logger: logging.Logger,
    output_func: OutputFunction,
) -> None:
    """Execute a metadata import through the EPM Automate adapter."""
    started_at = time.monotonic()
    password_file = settings.require_epm_automate_password_file()
    runner = EPMAutomateRunner(
        settings.epm_automate_executable,
        timeout=settings.epm_automate_command_timeout,
        logger=logger.getChild("epm_automate_runner"),
    )
    service = EPMAutomateMetadataService(
        runner,
        username=settings.epm_username,
        password_file=password_file,
        base_url=settings.epm_base_url,
        logger=logger.getChild("epm_automate_metadata_service"),
    )
    result = service.load_metadata(
        metadata_file=metadata_file,
        inbox_file_name=inbox_file_name,
        job_name=job_name,
        error_file_name=error_file_name,
    )
    elapsed = time.monotonic() - started_at
    logger.info(
        "EPM Automate metadata import successful: job='%s', "
        "execution_time=%.2fs.",
        result.job_name,
        elapsed,
    )
    output_func(
        "Metadata import completed successfully using EPM Automate. "
        f"Job: {result.job_name}; "
        f"File: {result.file_name}; "
        f"Replaced existing file: "
        f"{'Yes' if result.replaced_existing else 'No'}; "
        f"Execution time: {elapsed:.2f} seconds."
    )


def _execute_epm_automate_data_load(
    settings: Settings,
    *,
    data_file: Path | None,
    inbox_file_name: str | None,
    job_name: str,
    error_file_name: str | None,
    logger: logging.Logger,
    output_func: OutputFunction,
) -> None:
    """Execute a native Planning data import through EPM Automate."""
    started_at = time.monotonic()
    password_file = settings.require_epm_automate_password_file()
    runner = EPMAutomateRunner(
        settings.epm_automate_executable,
        timeout=settings.epm_automate_command_timeout,
        logger=logger.getChild("epm_automate_runner"),
    )
    service = EPMAutomateDataService(
        runner,
        username=settings.epm_username,
        password_file=password_file,
        base_url=settings.epm_base_url,
        logger=logger.getChild("epm_automate_data_service"),
    )
    result = service.load_data(
        data_file=data_file,
        inbox_file_name=inbox_file_name,
        job_name=job_name,
        error_file_name=error_file_name,
    )
    elapsed = time.monotonic() - started_at
    logger.info(
        "EPM Automate data import successful: job='%s', "
        "execution_time=%.2fs.",
        result.job_name,
        elapsed,
    )
    output_func(
        "Data import completed successfully using EPM Automate. "
        f"Job: {result.job_name}; "
        f"File: {result.file_name}; "
        f"Replaced existing file: "
        f"{'Yes' if result.replaced_existing else 'No'}; "
        f"Execution time: {elapsed:.2f} seconds."
    )


def _display_job_failure(
    error: JobFailedError,
    output_func: OutputFunction,
) -> None:
    job = error.job
    output_func(
        f"Planning job failed. Job ID: {job.job_id}; "
        f"Status: {job.descriptive_status or job.status}; "
        f"Details: {job.details or 'Not available.'}"
    )
    if error.diagnostics is None:
        return

    diagnostic_output = {
        "jobDetails": error.diagnostics.details,
        "messages": list(error.diagnostics.messages),
    }
    output_func(
        "Job diagnostics:\n"
        + json.dumps(diagnostic_output, indent=2, default=str)
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
