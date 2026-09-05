"""Planning process preflight and execution use cases."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.clients.epm_client import EPMClient
from app.config.email_settings import EmailNotificationSettings
from app.config.settings import Settings
from app.models.notification import (
    TaskNotificationStatus,
)
from app.models.pipeline import PipelineVariable
from app.models.pipeline_input import PipelineFileRequirement
from app.models.substitution_variable import (
    RequestedSubstitutionVariableUpdate,
)
from app.models.planning_cycle import PlanningCycle
from app.models.planning_process import (
    PlanningProcessDefinition,
    PlanningProcessStepDefinition,
    PlanningProcessStepType,
)
from app.models.workflow import WorkflowRun
from app.models.access_control import ExecutionActor
from app.services.form_service import PlanningFormService
from app.services.job_service import JobService
from app.services.notification_service import create_notification_service
from app.services.pipeline_preflight_service import PipelinePreflightService
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
from app.services.report_service import FormReportService
from app.services.substitution_variable_service import (
    SubstitutionVariableService,
)
from app.services.workflow_repository import SQLWorkflowRepository
from app.utils.exceptions import PlanningProcessError


@dataclass(frozen=True, slots=True)
class PlanningProcessInput:
    """Complete approved input set for one Planning process run."""

    process_code: str
    year: str
    start_period: str
    end_period: str
    scenario: str | None = None
    version: str | None = None
    run_data_map: bool | None = None
    clear_target: bool = False
    run_refresh: bool | None = None
    run_report: bool | None = None
    pipeline_variables: Mapping[str, str] = field(default_factory=dict)
    pipeline_uploads: Mapping[str, Path] = field(default_factory=dict)
    pipeline_inbox_files: Mapping[str, str] = field(default_factory=dict)
    variable_updates: tuple[
        RequestedSubstitutionVariableUpdate, ...
    ] = ()


@dataclass(frozen=True, slots=True)
class VariableChangePreview:
    scope: str
    name: str
    current_value: str
    requested_value: str
    changed: bool


@dataclass(frozen=True, slots=True)
class RuntimeVariablePreview:
    name: str
    display_name: str
    value: str | None
    required: bool
    source: str
    editable: bool


@dataclass(frozen=True, slots=True)
class FileRequirementPreview:
    key: str
    display_name: str
    configured_reference: str | None
    required: bool
    allowed_extensions: tuple[str, ...]
    consumers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProcessStepPreview:
    name: str
    step_type: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class PlanningProcessPreflight:
    """Structured, user-reviewable process preflight result."""

    process_code: str
    process_name: str
    cycle_code: str
    pipeline_code: str
    data_map_name: str | None
    report_name: str | None
    validation_enabled: bool
    run_refresh: bool
    run_data_map: bool
    clear_target: bool
    run_report: bool
    variable_changes: tuple[VariableChangePreview, ...]
    runtime_variables: tuple[RuntimeVariablePreview, ...]
    file_requirements: tuple[FileRequirementPreview, ...]
    steps: tuple[ProcessStepPreview, ...]


class PlanningProcessApplicationService:
    """Perform read-only live preflight for a configured Planning process."""

    def __init__(
        self,
        settings: Settings,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._logger = logger or logging.getLogger(__name__)

    def get_definition(self, code: str) -> PlanningProcessDefinition:
        """Return one configured process for presentation."""
        return PlanningProcessCatalogService(
            self._settings.database_target
        ).get(
            self._settings.planning_process_catalog_file,
            code,
        )

    def preflight(
        self,
        process_input: PlanningProcessInput,
    ) -> PlanningProcessPreflight:
        """Validate live Oracle artifacts without changing Oracle state."""
        definition = self.get_definition(process_input.process_code)
        cycle_definition = PlanningCycleCatalogService(
            self._settings.database_target
        ).get(
            self._settings.planning_cycle_catalog_file,
            definition.cycle_code,
        )
        pipeline_only = self._is_pipeline_only(definition)
        cycle = self._cycle(
            process_input,
            require_cycle_values=not pipeline_only,
        )
        data_map_step = self._step(
            definition,
            PlanningProcessStepType.RUN_DATA_MAP,
        )
        run_data_map = (
            bool(data_map_step and data_map_step.enabled_by_default)
            if process_input.run_data_map is None
            else process_input.run_data_map
        )
        if run_data_map and data_map_step is None:
            raise PlanningProcessError(
                "The selected process does not define a Data Map step."
            )
        refresh_step = self._step(
            definition,
            PlanningProcessStepType.REFRESH_CUBE,
        )
        report_step = self._step(
            definition,
            PlanningProcessStepType.GENERATE_REPORT,
        )
        validation_step = self._step(
            definition,
            PlanningProcessStepType.VALIDATE_DATA,
        )
        run_refresh = (
            bool(refresh_step and refresh_step.enabled_by_default)
            if process_input.run_refresh is None
            else process_input.run_refresh
        )
        run_report = (
            bool(report_step and report_step.enabled_by_default)
            if process_input.run_report is None
            else process_input.run_report
        )
        source_form = self._settings.validation_source_form
        target_form = self._settings.validation_target_form
        validation_enabled = bool(
            validation_step
            and run_data_map
            and source_form
            and target_form
        )

        with EPMClient(
            self._settings,
            logger=self._logger.getChild("client"),
        ) as client:
            client.authenticate()
            variable_service = SubstitutionVariableService(
                client,
                logger=self._logger.getChild("variables"),
            )
            variables = (
                variable_service.get_all_variables()
                if (
                    cycle_definition.variable_bindings
                    or process_input.variable_updates
                )
                else ()
            )
            pipeline_details = PipelineService(
                client,
                logger=self._logger.getChild("pipeline"),
            ).get_pipeline_details(cycle_definition.pipeline_code)
            available_data_maps = ()
            if cycle_definition.data_map_name:
                available_data_maps = tuple(
                    job.job_name
                    for job in JobService(client).get_job_definitions(
                        job_type="PLAN_TYPE_MAP"
                    )
                )
            if run_refresh and refresh_step is not None:
                refresh_name = str(
                    refresh_step.parameters.get("jobName", "")
                ).strip()
                available_refresh_jobs = tuple(
                    job.job_name
                    for job in JobService(client).get_job_definitions(
                        job_type="CUBE_REFRESH"
                    )
                )
                if refresh_name.casefold() not in {
                    name.casefold() for name in available_refresh_jobs
                }:
                    raise PlanningProcessError(
                        f"Cube Refresh job '{refresh_name}' was not found."
                    )
            cycle_result = PlanningCyclePreflightService().validate(
                cycle_definition,
                cycle,
                variables=variables,
                pipeline_details=pipeline_details,
                available_data_maps=available_data_maps,
                variable_service=variable_service,
                require_cycle_values=not pipeline_only,
            )
            if process_input.variable_updates:
                requested_updates = variable_service.build_safe_updates(
                    process_input.variable_updates,
                    current_variables=variables,
                )
                resolved_updates = variable_service.merge_updates(
                    cycle_result.updates,
                    requested_updates,
                )
            else:
                resolved_updates = cycle_result.updates
            file_requirements = PipelinePreflightService(
                logger=self._logger.getChild("pipeline_files"),
            ).discover_file_requirements(pipeline_details)

            report_name = None
            if run_report:
                if report_step is None:
                    raise PlanningProcessError(
                        "The selected process does not define a report step."
                    )
                report_name = str(
                    report_step.parameters.get("reportName", "")
                ).strip()
                FormReportService(
                    client,
                    form_service=PlanningFormService(client),
                    catalog_file=self._settings.report_catalog_file,
                    logger=self._logger.getChild("report"),
                ).get_form_layout(report_name)

        resolved_runtime = dict(process_input.pipeline_variables)
        resolved_runtime.update(dict(cycle_result.pipeline_variables))
        runtime_variables = self._runtime_variables(
            pipeline_details.variables,
            file_requirements,
            resolved_runtime,
        )
        options = {
            PlanningProcessStepType.UPDATE_VARIABLES: any(
                update.is_changed for update in resolved_updates
            ),
            PlanningProcessStepType.REFRESH_CUBE: run_refresh,
            PlanningProcessStepType.RUN_DATA_MAP: run_data_map,
            PlanningProcessStepType.VALIDATE_DATA: validation_enabled,
            PlanningProcessStepType.GENERATE_REPORT: run_report,
        }
        return PlanningProcessPreflight(
            process_code=definition.code,
            process_name=definition.display_name,
            cycle_code=definition.cycle_code,
            pipeline_code=cycle_definition.pipeline_code,
            data_map_name=(
                cycle_definition.data_map_name if run_data_map else None
            ),
            report_name=report_name,
            validation_enabled=validation_enabled,
            run_refresh=run_refresh,
            run_data_map=run_data_map,
            clear_target=bool(process_input.clear_target and run_data_map),
            run_report=run_report,
            variable_changes=tuple(
                VariableChangePreview(
                    scope=update.scope,
                    name=update.name,
                    current_value=update.old_value,
                    requested_value=update.new_value,
                    changed=update.is_changed,
                )
                for update in resolved_updates
            ),
            runtime_variables=runtime_variables,
            file_requirements=tuple(
                self._file_requirement(item)
                for item in file_requirements
            ),
            steps=tuple(
                ProcessStepPreview(
                    name=step.name,
                    step_type=step.step_type.value,
                    enabled=options.get(
                        step.step_type,
                        step.enabled_by_default,
                    ),
                )
                for step in self._execution_steps(
                    definition,
                    include_variable_update=bool(resolved_updates),
                )
            ),
        )

    @staticmethod
    def _execution_steps(
        definition: PlanningProcessDefinition,
        *,
        include_variable_update: bool,
    ):
        """Include the optional update stage for legacy definitions."""
        steps = list(definition.steps)
        if not include_variable_update or any(
            step.step_type is PlanningProcessStepType.UPDATE_VARIABLES
            for step in steps
        ):
            return tuple(steps)
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
        return tuple(steps)

    @staticmethod
    def _cycle(
        process_input: PlanningProcessInput,
        *,
        require_cycle_values: bool = True,
    ) -> PlanningCycle:
        values = {
            "process code": process_input.process_code,
            "year": process_input.year,
        }
        if require_cycle_values:
            values.update(
                {
                    "start period": process_input.start_period,
                    "end period": process_input.end_period,
                }
            )
        missing = [
            label for label, value in values.items() if not str(value).strip()
        ]
        if missing:
            raise PlanningProcessError(
                "Planning process input requires: " + ", ".join(missing)
            )
        return PlanningCycle(
            year=str(process_input.year).strip(),
            start_period=str(process_input.start_period).strip(),
            end_period=str(process_input.end_period).strip(),
            scenario=(
                str(process_input.scenario).strip()
                if process_input.scenario
                else None
            ),
            version=(
                str(process_input.version).strip()
                if process_input.version
                else None
            ),
        )

    @staticmethod
    def _is_pipeline_only(
        definition: PlanningProcessDefinition,
    ) -> bool:
        preflight = PlanningProcessApplicationService._step(
            definition,
            PlanningProcessStepType.PREFLIGHT,
        )
        return bool(
            preflight
            and preflight.parameters.get("pipelineOnly") is True
        )

    @staticmethod
    def _runtime_variables(
        variables: tuple[PipelineVariable, ...],
        file_requirements: tuple[PipelineFileRequirement, ...],
        supplied: Mapping[str, str],
    ) -> tuple[RuntimeVariablePreview, ...]:
        supplied_by_name = {
            str(name).casefold(): str(value).strip()
            for name, value in supplied.items()
            if str(value).strip()
        }
        file_names = {
            requirement.variable_name.casefold()
            for requirement in file_requirements
            if requirement.variable_name
        }
        result: list[RuntimeVariablePreview] = []
        missing: list[str] = []
        for variable in variables:
            key = variable.name.casefold()
            if key in file_names:
                continue
            if key in supplied_by_name:
                value = supplied_by_name[key]
                source = (
                    "Planning cycle"
                    if variable.name.upper()
                    in {
                        "YEAR",
                        "STARTPERIOD",
                        "ENDPERIOD",
                        "SCENARIO",
                        "VERSION",
                    }
                    else "Run input"
                )
            else:
                value = variable.default_value
                source = "Pipeline default" if value is not None else "None"
            required = variable.requires_value
            if required and not value:
                missing.append(variable.display_name)
            result.append(
                RuntimeVariablePreview(
                    name=variable.name,
                    display_name=variable.display_name,
                    value=value,
                    required=required,
                    source=source,
                    editable=variable.name.upper()
                    not in {
                        "YEAR",
                        "STARTPERIOD",
                        "ENDPERIOD",
                        "SCENARIO",
                        "VERSION",
                    },
                )
            )
        if missing:
            raise PlanningProcessError(
                "Required Pipeline runtime values are missing: "
                + ", ".join(missing)
            )
        return tuple(result)

    @staticmethod
    def _file_requirement(
        requirement: PipelineFileRequirement,
    ) -> FileRequirementPreview:
        return FileRequirementPreview(
            key=requirement.key,
            display_name=requirement.display_name,
            configured_reference=requirement.configured_reference,
            required=requirement.required,
            allowed_extensions=tuple(sorted(requirement.allowed_extensions)),
            consumers=tuple(
                consumer.display_label
                for consumer in requirement.consumers
            ),
        )

    @staticmethod
    def _step(
        definition: PlanningProcessDefinition,
        step_type: PlanningProcessStepType,
    ):
        return next(
            (
                step
                for step in definition.steps
                if step.step_type is step_type
            ),
            None,
        )


class PlanningProcessCommandExecutor:
    """Execute the existing governed CLI process from another interface."""

    def __init__(
        self,
        settings: Settings,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._logger = logger or logging.getLogger(__name__)

    def execute(
        self,
        process_input: PlanningProcessInput,
        *,
        execution_id: str,
        log_file: Path,
        actor: ExecutionActor | None = None,
    ) -> WorkflowRun:
        """Run the shared process implementation and return its record."""
        from main import (
            _build_task_notification,
            _interactive_arguments,
            _run_planning_process_command,
        )

        arguments = _interactive_arguments(
            command="process",
            metadata_engine="rest",
        )
        arguments.process_code = process_input.process_code
        arguments.year = process_input.year
        arguments.start_period = process_input.start_period
        arguments.end_period = process_input.end_period
        arguments.scenario = process_input.scenario
        arguments.cycle_version = process_input.version
        arguments.skip_data_map = process_input.run_data_map is False
        arguments.clear_target = process_input.clear_target
        arguments.process_run_refresh = process_input.run_refresh
        arguments.skip_report = process_input.run_report is False
        arguments.pipeline_variables = [
            f"{name}={value}"
            for name, value in process_input.pipeline_variables.items()
        ]
        arguments.pipeline_uploads = [
            f"{name}={path}"
            for name, path in process_input.pipeline_uploads.items()
        ]
        arguments.pipeline_inbox_files = [
            f"{name}={value}"
            for name, value in process_input.pipeline_inbox_files.items()
        ]
        setattr(
            arguments,
            "_process_variable_update_requests",
            process_input.variable_updates,
        )
        setattr(arguments, "_execution_id_override", execution_id)
        log_file.parent.mkdir(parents=True, exist_ok=True)

        email_settings = self._settings.email_notifications
        if not isinstance(email_settings, EmailNotificationSettings):
            email_settings = EmailNotificationSettings()
        notifications = create_notification_service(
            email_settings,
            logger=self._logger.getChild("notification_service"),
        )
        started_at = time.monotonic()

        def output(message: str) -> None:
            normalized = str(message).rstrip()
            with log_file.open("a", encoding="utf-8") as stream:
                stream.write(normalized + "\n")

        try:
            exit_code = _run_planning_process_command(
                arguments,
                settings=self._settings,
                interactive=False,
                input_func=lambda prompt: "",
                output_func=output,
                logger=self._logger,
            )
            if exit_code != 0:
                raise PlanningProcessError(
                    f"Planning process returned exit code {exit_code}."
                )
        except Exception as exc:
            notifications.publish(
                _build_task_notification(
                    "process",
                    arguments,
                    settings=self._settings,
                    status=TaskNotificationStatus.FAILED,
                    duration_seconds=time.monotonic() - started_at,
                    error=exc,
                )
            )
            raise

        notifications.publish(
            _build_task_notification(
                "process",
                arguments,
                settings=self._settings,
                status=TaskNotificationStatus.SUCCESS,
                duration_seconds=time.monotonic() - started_at,
            )
        )
        run = SQLWorkflowRepository(
            self._settings.database_target
        ).get(execution_id)
        if run is None:
            raise PlanningProcessError(
                f"Planning process '{execution_id}' did not create a "
                "workflow history record."
            )
        run = replace(
            run,
            oracle_execution_username=(
                self._settings.oracle_execution_username
            ),
            **(
                {
                    "initiated_by": actor.username,
                    "initiated_by_display": actor.display_name,
                    "trigger_source": actor.trigger_source,
                }
                if actor is not None
                else {}
            ),
        )
        SQLWorkflowRepository(
            self._settings.database_target
        ).save(run)
        return run
