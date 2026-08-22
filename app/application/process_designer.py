"""Use cases for designing governed Oracle Pipeline processes."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping

from app.application.operations import (
    OperationCatalogService,
    PipelineOperationPreview,
)
from app.config.settings import Settings
from app.models.pipeline_process_design import (
    ProcessArchitectureFinding,
    ProcessArchitectureReview,
    ProcessStepOwnership,
    PipelineRunProfile,
    PipelineProcessSummary,
    PipelineProcessVersion,
)
from app.models.planning_cycle import PlanningCycleDefinition
from app.application.planning_process import PlanningProcessInput
from app.models.planning_process import (
    PlanningProcessDefinition,
    PlanningProcessStepDefinition,
    PlanningProcessStepType,
    ProcessContextMode,
)
from app.services.pipeline_catalog_service import PipelineCatalogService
from app.services.pipeline_process_repository import (
    SQLPipelineProcessRepository,
)
from app.services.planning_process_catalog_service import (
    PlanningProcessCatalogService,
)
from app.services.planning_cycle_catalog_service import (
    PlanningCycleCatalogService,
)
from app.utils.exceptions import ConfigurationError

_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,49}$")


@dataclass(frozen=True, slots=True)
class PipelineProcessDraftInput:
    """Administrator input for one published Oracle Pipeline process."""

    code: str
    display_name: str
    pipeline_code: str
    context_mode: ProcessContextMode = ProcessContextMode.PROMPT_EACH_RUN


@dataclass(frozen=True, slots=True)
class PipelineRunProfileInput:
    """Optional reusable run preset captured by Process Designer."""

    name: str
    year: str = ""
    start_period: str = ""
    end_period: str = ""
    scenario: str | None = None
    version: str | None = None
    pipeline_variables: Mapping[str, str] = field(default_factory=dict)
    inbox_files: Mapping[str, str] = field(default_factory=dict)
    required_upload_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DesignerProcessItem:
    """Unified library item for built-in and designer-managed processes."""

    code: str
    display_name: str
    pipeline_code: str
    designer_managed: bool
    legacy_backed: bool
    latest_version: int | None
    active_version: int | None
    profile_count: int
    context_mode: ProcessContextMode


class ProcessDesignerApplicationService:
    """Create, validate, version, and publish Pipeline process wrappers."""

    def __init__(
        self,
        settings: Settings,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._logger = logger or logging.getLogger(__name__)
        self._repository = SQLPipelineProcessRepository(
            settings.database_target
        )
        self._operations = OperationCatalogService(
            settings,
            logger=self._logger.getChild("pipeline_discovery"),
        )

    def list_pipelines(self):
        """Return administrator-registered Oracle Pipelines."""
        return PipelineCatalogService(
            self._settings.database_target
        ).load(
            self._settings.pipeline_catalog_file
        )

    def list_processes(self) -> tuple[PipelineProcessSummary, ...]:
        """Return all designer-managed process summaries."""
        return self._repository.list_summaries()

    def list_workspace_processes(self) -> tuple[DesignerProcessItem, ...]:
        """Return every runnable process in one management library."""
        legacy_codes = {
            item.code.casefold()
            for item in PlanningProcessCatalogService().load(
                self._settings.planning_process_catalog_file
            )
        }
        definitions = PlanningProcessCatalogService(
            self._settings.database_target
        ).load(self._settings.planning_process_catalog_file)
        cycles = {
            item.code.casefold(): item
            for item in PlanningCycleCatalogService(
                self._settings.database_target
            ).load(self._settings.planning_cycle_catalog_file)
        }
        managed = {
            item.code.casefold(): item for item in self.list_processes()
        }
        items = [
            DesignerProcessItem(
                code=definition.code,
                display_name=(
                    managed[definition.code.casefold()].display_name
                    if definition.code.casefold() in managed
                    else definition.display_name
                ),
                pipeline_code=(
                    managed[definition.code.casefold()].pipeline_code
                    if definition.code.casefold() in managed
                    else cycles[
                        definition.cycle_code.casefold()
                    ].pipeline_code
                ),
                designer_managed=definition.code.casefold() in managed,
                legacy_backed=definition.code.casefold() in legacy_codes,
                latest_version=(
                    managed[definition.code.casefold()].latest_version
                    if definition.code.casefold() in managed
                    else None
                ),
                active_version=(
                    managed[definition.code.casefold()].active_version
                    if definition.code.casefold() in managed
                    else None
                ),
                profile_count=len(
                    self._repository.list_profiles(definition.code)
                ),
                context_mode=(
                    managed[definition.code.casefold()].context_mode
                    if definition.code.casefold() in managed
                    else definition.context_mode
                ),
            )
            for definition in definitions
        ]
        known_codes = {item.code.casefold() for item in items}
        items.extend(
            DesignerProcessItem(
                code=summary.code,
                display_name=summary.display_name,
                pipeline_code=summary.pipeline_code,
                designer_managed=True,
                legacy_backed=False,
                latest_version=summary.latest_version,
                active_version=summary.active_version,
                profile_count=len(
                    self._repository.list_profiles(summary.code)
                ),
                context_mode=summary.context_mode,
            )
            for summary in managed.values()
            if summary.code.casefold() not in known_codes
        )
        return tuple(items)

    def get_active(
        self,
        code: str,
    ) -> PipelineProcessVersion | None:
        """Return the active designer-managed process version."""
        return self._repository.get_active(code)

    def architecture_review(
        self,
        code: str,
    ) -> ProcessArchitectureReview:
        """Classify current steps without changing the Process or Oracle."""
        normalized = str(code).strip()
        latest = self._repository.get_latest(normalized)
        if latest is not None:
            definition = latest.process
            cycle = latest.cycle
        else:
            definition = PlanningProcessCatalogService(
                self._settings.database_target
            ).get(
                self._settings.planning_process_catalog_file,
                normalized,
            )
            cycle = PlanningCycleCatalogService(
                self._settings.database_target
            ).get(
                self._settings.planning_cycle_catalog_file,
                definition.cycle_code,
            )
        managed = latest is not None
        pipeline_step = next(
            (
                step
                for step in definition.steps
                if step.step_type is PlanningProcessStepType.RUN_PIPELINE
            ),
            None,
        )
        parameters = pipeline_step.parameters if pipeline_step else {}
        raw_stages = parameters.get("pipelineStages") or ()
        pipeline_stages = tuple(
            str(
                item.get("displayName") or item.get("name") or ""
            ).strip()
            for item in raw_stages
            if isinstance(item, Mapping)
            and str(item.get("displayName") or item.get("name") or "").strip()
        )
        raw_variables = parameters.get("runtimeVariableNames") or ()
        runtime_variables = tuple(
            str(item).strip()
            for item in raw_variables
            if str(item).strip()
        )
        return ProcessArchitectureReview(
            process_code=definition.code,
            process_name=definition.display_name,
            pipeline_code=cycle.pipeline_code,
            designer_managed=managed,
            source_version=latest.version if latest else None,
            pipeline_only=self._is_thin_process(definition),
            pipeline_stages=pipeline_stages,
            runtime_variables=runtime_variables,
            findings=tuple(
                self._architecture_finding(index, step)
                for index, step in enumerate(definition.steps, start=1)
            ),
        )

    def prepare_migration_draft(
        self,
        code: str,
    ) -> PipelineProcessVersion:
        """Prepare one offline thin draft without changing Oracle state."""
        normalized = str(code).strip()
        active = self._repository.get_active(normalized)
        latest = self._repository.get_latest(normalized)
        if latest is not None and self._is_thin_process(latest.process):
            return latest

        if active is not None:
            source_process = active.process
            source_cycle = active.cycle
        else:
            source_process = PlanningProcessCatalogService().get(
                self._settings.planning_process_catalog_file,
                normalized,
            )
            source_cycle = PlanningCycleCatalogService().get(
                self._settings.planning_cycle_catalog_file,
                source_process.cycle_code,
            )
        process, cycle = self._thin_definitions_from_existing(
            source_process,
            source_cycle,
        )
        if latest is not None and (
            latest.process == process and latest.cycle == cycle
        ):
            return latest
        return self._repository.save_or_replace_draft(process, cycle)

    def list_versions(
        self,
        code: str,
    ) -> tuple[PipelineProcessVersion, ...]:
        """Return all versions for a designer-managed process."""
        return self._repository.list_versions(code)

    def list_profiles(
        self,
        code: str,
    ) -> tuple[PipelineRunProfile, ...]:
        """Return optional saved run presets for the selected process."""
        return self._repository.list_profiles(code)

    def get_profile(
        self,
        code: str,
        profile_id: int,
    ) -> PipelineRunProfile:
        """Return one saved profile or raise a presentation-safe error."""
        profile = self._repository.get_profile(code, profile_id)
        if profile is None:
            raise ConfigurationError(
                f"Run preset {profile_id} was not found for '{code}'."
            )
        return profile

    def archive_profile(
        self,
        code: str,
        profile_id: int,
    ) -> PipelineRunProfile:
        """Archive a run preset without deleting its audit record."""
        return self._repository.archive_profile(code, profile_id)

    def inspect_pipeline(self, code: str) -> PipelineOperationPreview:
        """Inspect the current Oracle Pipeline definition."""
        return self._operations.preflight_pipeline(code)

    def save_draft(
        self,
        draft: PipelineProcessDraftInput,
    ) -> PipelineProcessVersion:
        """Create the next immutable version of a Pipeline process."""
        code, name, pipeline_code, context_mode = self._validated_identity(
            draft
        )
        built_in = PlanningProcessCatalogService().load(
            self._settings.planning_process_catalog_file
        )
        if code.casefold() in {item.code.casefold() for item in built_in}:
            raise ConfigurationError(
                f"Process code '{code}' belongs to a built-in process. "
                "Choose a new code for the designer-managed process."
            )
        if self._repository.get_latest(code) is not None:
            raise ConfigurationError(
                f"Process '{code}' already exists. Use Edit process to "
                "change its configuration."
            )
        preview = self.inspect_pipeline(pipeline_code)
        process, cycle = self._definitions(
            code,
            name,
            pipeline_code,
            context_mode,
            draft,
            preview,
        )
        return self._repository.save_draft(process, cycle)

    def save_revision(
        self,
        code: str,
        revision: PipelineProcessDraftInput,
    ) -> PipelineProcessVersion:
        """Save only a meaningful change, reusing an existing open draft."""
        process_code = str(code).strip().upper()
        supplied_code = str(revision.code).strip().upper()
        if supplied_code and supplied_code != process_code:
            raise ConfigurationError("A process code cannot be changed.")
        latest = self._repository.get_latest(process_code)
        if latest is None:
            raise ConfigurationError(
                f"Designer-managed process '{process_code}' was not found."
            )
        _, name, pipeline_code, context_mode = self._validated_identity(
            PipelineProcessDraftInput(
                code=process_code,
                display_name=revision.display_name,
                pipeline_code=revision.pipeline_code,
                context_mode=revision.context_mode,
            )
        )
        preview = self.inspect_pipeline(pipeline_code)
        process, cycle = self._definitions(
            process_code,
            name,
            pipeline_code,
            context_mode,
            revision,
            preview,
        )
        if latest.process == process and latest.cycle == cycle:
            raise ConfigurationError(
                "No configuration changes were detected. The existing "
                "draft was kept unchanged."
            )
        return self._repository.save_or_replace_draft(process, cycle)

    def activate(self, code: str, version: int) -> PipelineProcessVersion:
        """Verify Oracle state and publish the selected process version."""
        candidate = self._repository.get(code, version)
        if candidate is None:
            raise ConfigurationError(
                f"Pipeline process '{code}' version {version} was not found."
            )
        preview = self.inspect_pipeline(candidate.cycle.pipeline_code)
        pipeline_step = next(
            (
                step
                for step in candidate.process.steps
                if step.step_type is PlanningProcessStepType.RUN_PIPELINE
            ),
            None,
        )
        stored_stages = (
            pipeline_step.parameters.get("pipelineStages")
            if pipeline_step is not None
            else None
        )
        if stored_stages and self._stage_signature(
            stored_stages
        ) != self._preview_stage_signature(preview):
            raise ConfigurationError(
                "The Oracle Pipeline stages changed after this draft was "
                "saved. Edit the process to capture the current definition "
                "before activation."
            )
        return self._repository.activate(code, version)

    def deactivate(self, code: str) -> PipelineProcessVersion:
        """Remove a process from runnable catalogs without deleting it."""
        return self._repository.deactivate(code)

    def save_profile(
        self,
        process_code: str,
        profile_input: PipelineRunProfileInput,
    ) -> PipelineRunProfile:
        """Validate live inputs and persist one optional run preset."""
        active = self._repository.get_active(process_code)
        if active is None:
            raise ConfigurationError(
                "Run presets require an active designer-managed process."
            )
        name = str(profile_input.name).strip()
        if not name:
            raise ConfigurationError("Run preset name is required.")
        year = str(profile_input.year).strip()
        if not year:
            raise ConfigurationError(
                "Planning year is required for every run preset."
            )
        preview = self.inspect_pipeline(active.cycle.pipeline_code)
        supplied_variables = self._normalize_mapping(
            profile_input.pipeline_variables or {},
            label="Pipeline variable",
        )
        supplied_inbox = self._normalize_mapping(
            profile_input.inbox_files or {},
            label="Inbox file",
        )
        upload_keys = tuple(
            dict.fromkeys(
                str(key).strip()
                for key in profile_input.required_upload_keys
                if str(key).strip()
            )
        )
        live_variables = {
            item.name.casefold(): item for item in preview.variables
        }
        unknown_variables = [
            name
            for name in supplied_variables
            if name.casefold() not in live_variables
        ]
        if unknown_variables:
            raise ConfigurationError(
                "Run preset contains unknown Pipeline variables: "
                + ", ".join(unknown_variables)
            )
        canonical_variables = {
            live_variables[name.casefold()].name: value
            for name, value in supplied_variables.items()
        }
        cycle_runtime_values = {
            "YEAR": year,
            "STARTPERIOD": str(profile_input.start_period).strip(),
            "ENDPERIOD": str(profile_input.end_period).strip(),
            "SCENARIO": str(profile_input.scenario or "").strip(),
            "VERSION": str(profile_input.version or "").strip(),
        }
        missing_variables = [
            item.display_name
            for item in preview.variables
            if item.required
            and not item.default_value
            and not cycle_runtime_values.get(item.name.upper())
            and item.name.casefold()
            not in {
                name.casefold() for name in canonical_variables
            }
        ]
        if missing_variables:
            raise ConfigurationError(
                "Required Pipeline variables need preset values: "
                + ", ".join(missing_variables)
            )

        live_files = {
            item.key.casefold(): item
            for item in preview.file_requirements
        }
        supplied_file_keys = {
            key.casefold() for key in supplied_inbox
        } | {key.casefold() for key in upload_keys}
        unknown_files = supplied_file_keys - set(live_files)
        if unknown_files:
            raise ConfigurationError(
                "Run preset contains unknown file inputs: "
                + ", ".join(sorted(unknown_files))
            )
        canonical_inbox = {
            live_files[name.casefold()].key: value
            for name, value in supplied_inbox.items()
        }
        canonical_uploads = tuple(
            live_files[key.casefold()].key for key in upload_keys
        )
        duplicate_files = {
            key.casefold() for key in canonical_inbox
        } & {key.casefold() for key in canonical_uploads}
        if duplicate_files:
            raise ConfigurationError(
                "A preset file cannot use both Inbox and runtime upload."
            )
        missing_files = [
            item.display_name
            for item in preview.file_requirements
            if item.required
            and not item.configured_reference
            and item.key.casefold() not in supplied_file_keys
        ]
        if missing_files:
            raise ConfigurationError(
                "Required Pipeline files need an Inbox reference or runtime "
                "upload strategy: " + ", ".join(missing_files)
            )
        return self._repository.save_profile(
            process_code=active.process.code,
            name=name,
            year=year,
            start_period=str(profile_input.start_period).strip(),
            end_period=str(profile_input.end_period).strip(),
            scenario=self._optional(profile_input.scenario),
            version=self._optional(profile_input.version),
            pipeline_variables=canonical_variables,
            inbox_files=canonical_inbox,
            required_upload_keys=canonical_uploads,
        )

    def profile_process_input(
        self,
        process_code: str,
        profile_id: int,
        *,
        upload_paths: Mapping[str, Path] | None = None,
    ) -> PlanningProcessInput:
        """Resolve a saved profile into the shared execution input model."""
        profile = self._repository.get_profile(process_code, profile_id)
        if profile is None:
            raise ConfigurationError(
                f"Run preset {profile_id} was not found for "
                f"'{process_code}'."
            )
        return PlanningProcessInput(
            process_code=profile.process_code,
            year=profile.year,
            start_period=profile.start_period,
            end_period=profile.end_period,
            scenario=profile.scenario,
            version=profile.version,
            pipeline_variables=dict(profile.pipeline_variables),
            pipeline_uploads=dict(upload_paths or {}),
            pipeline_inbox_files=dict(profile.inbox_files),
        )

    def validate_profile(
        self,
        process_code: str,
        profile_id: int,
    ) -> PipelineOperationPreview:
        """Detect live Pipeline drift before a saved profile is executed."""
        active = self._repository.get_active(process_code)
        if active is None:
            raise ConfigurationError(
                f"Process '{process_code}' does not have an active version."
            )
        profile = self.get_profile(process_code, profile_id)
        if not profile.year.strip():
            raise ConfigurationError(
                "This saved run preset has no Planning year. Recreate the "
                "preset with a year before running or scheduling it."
            )
        preview = self.inspect_pipeline(active.cycle.pipeline_code)
        live_variables = {
            item.name.casefold(): item for item in preview.variables
        }
        stored_variables = dict(profile.pipeline_variables)
        unknown_variables = [
            name
            for name in stored_variables
            if name.casefold() not in live_variables
        ]
        if unknown_variables:
            raise ConfigurationError(
                "The Oracle Pipeline changed after this preset was saved. "
                "Unknown variables: " + ", ".join(unknown_variables)
            )
        cycle_values = {
            "YEAR": profile.year,
            "STARTPERIOD": profile.start_period,
            "ENDPERIOD": profile.end_period,
            "SCENARIO": profile.scenario or "",
            "VERSION": profile.version or "",
        }
        stored_names = {
            name.casefold() for name in stored_variables
        }
        missing_variables = [
            item.display_name
            for item in preview.variables
            if item.required
            and not item.default_value
            and not cycle_values.get(item.name.upper())
            and item.name.casefold() not in stored_names
        ]
        if missing_variables:
            raise ConfigurationError(
                "The live Pipeline now requires preset values for: "
                + ", ".join(missing_variables)
            )
        live_files = {
            item.key.casefold(): item
            for item in preview.file_requirements
        }
        stored_file_keys = {
            key.casefold() for key, _ in profile.inbox_files
        } | {key.casefold() for key in profile.required_upload_keys}
        unknown_files = stored_file_keys - set(live_files)
        if unknown_files:
            raise ConfigurationError(
                "The Oracle Pipeline changed after this preset was saved. "
                "Unknown file inputs: " + ", ".join(sorted(unknown_files))
            )
        missing_files = [
            item.display_name
            for item in preview.file_requirements
            if item.required
            and not item.configured_reference
            and item.key.casefold() not in stored_file_keys
        ]
        if missing_files:
            raise ConfigurationError(
                "The live Pipeline now requires file strategies for: "
                + ", ".join(missing_files)
            )
        return preview

    @staticmethod
    def _normalize_mapping(
        values: Mapping[str, str],
        *,
        label: str,
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        for raw_name, raw_value in values.items():
            name = str(raw_name).strip()
            value = str(raw_value).strip()
            if not name or not value:
                raise ConfigurationError(
                    f"{label} names and values cannot be empty."
                )
            result[name] = value
        return result

    @staticmethod
    def _architecture_finding(
        sequence: int,
        step: PlanningProcessStepDefinition,
    ) -> ProcessArchitectureFinding:
        """Return the target owner for one existing wrapper step."""
        step_type = step.step_type
        if step_type is PlanningProcessStepType.PREFLIGHT:
            ownership = ProcessStepOwnership.PLATFORM_GATEWAY
            recommendation = (
                "Keep authentication, permission, required-input, file, "
                "and approval checks in the platform."
            )
        elif step_type is PlanningProcessStepType.RUN_PIPELINE:
            ownership = ProcessStepOwnership.PLATFORM_GATEWAY
            recommendation = (
                "Keep one governed call that runs and monitors the Oracle "
                "Pipeline."
            )
        elif step_type in {
            PlanningProcessStepType.UPDATE_VARIABLES,
            PlanningProcessStepType.REFRESH_CUBE,
            PlanningProcessStepType.RUN_BUSINESS_RULE,
        }:
            ownership = ProcessStepOwnership.ORACLE_PIPELINE
            recommendation = (
                "Configure this technical action as an Oracle Pipeline "
                "stage, verify it there, then remove the wrapper step."
            )
        elif step_type is PlanningProcessStepType.RUN_DATA_MAP:
            ownership = ProcessStepOwnership.REVIEW_REQUIRED
            recommendation = (
                "Confirm the equivalent Data Map or Plan Type Map stage "
                "exists in Oracle Pipeline before removing this step."
            )
        elif step_type in {
            PlanningProcessStepType.VALIDATE_DATA,
            PlanningProcessStepType.GENERATE_REPORT,
        }:
            ownership = ProcessStepOwnership.PLATFORM_EXTENSION
            recommendation = (
                "Retain only when this is an approved post-run output or "
                "control that Oracle Pipeline does not provide."
            )
        else:
            ownership = ProcessStepOwnership.REVIEW_REQUIRED
            recommendation = (
                "Confirm ownership and business purpose before migration."
            )
        return ProcessArchitectureFinding(
            sequence=sequence,
            step_type=step_type,
            step_name=step.name,
            enabled=step.enabled_by_default,
            ownership=ownership,
            recommendation=recommendation,
        )

    @staticmethod
    def _is_thin_process(
        definition: PlanningProcessDefinition,
    ) -> bool:
        """Return whether only the platform gateway and Pipeline remain."""
        return tuple(step.step_type for step in definition.steps) == (
            PlanningProcessStepType.PREFLIGHT,
            PlanningProcessStepType.RUN_PIPELINE,
        ) and bool(
            definition.steps[0].parameters.get("pipelineOnly") is True
        )

    @staticmethod
    def _thin_definitions_from_existing(
        process: PlanningProcessDefinition,
        cycle: PlanningCycleDefinition,
    ) -> tuple[PlanningProcessDefinition, PlanningCycleDefinition]:
        """Build a non-active migration target from stored definitions."""
        pipeline_steps = tuple(
            step
            for step in process.steps
            if step.step_type is PlanningProcessStepType.RUN_PIPELINE
        )
        if len(pipeline_steps) != 1:
            raise ConfigurationError(
                f"Process '{process.code}' must contain exactly one Oracle "
                "Pipeline step before it can be simplified."
            )
        pipeline_step = pipeline_steps[0]
        return (
            PlanningProcessDefinition(
                code=process.code,
                display_name=process.display_name,
                cycle_code=process.cycle_code,
                steps=(
                    PlanningProcessStepDefinition(
                        step_type=PlanningProcessStepType.PREFLIGHT,
                        name="Validate Process Inputs",
                        parameters={"pipelineOnly": True},
                    ),
                    PlanningProcessStepDefinition(
                        step_type=PlanningProcessStepType.RUN_PIPELINE,
                        name="Run Oracle Pipeline",
                        parameters=dict(pipeline_step.parameters),
                    ),
                ),
                context_mode=process.context_mode,
            ),
            PlanningCycleDefinition(
                code=cycle.code,
                display_name=cycle.display_name,
                pipeline_code=cycle.pipeline_code,
                data_map_name=None,
                variable_bindings=(),
            ),
        )

    @staticmethod
    def _optional(value: str | None) -> str | None:
        normalized = str(value).strip() if value is not None else ""
        return normalized or None

    @staticmethod
    def _stage_signature(raw_stages) -> tuple[tuple[object, ...], ...]:
        return tuple(
            (
                str(item.get("name", "")).strip().casefold(),
                str(item.get("displayName", "")).strip().casefold(),
                int(item.get("jobCount", 0)),
                bool(item.get("runsInParallel", False)),
            )
            for item in raw_stages
            if isinstance(item, Mapping)
        )

    @staticmethod
    def _preview_stage_signature(
        preview: PipelineOperationPreview,
    ) -> tuple[tuple[object, ...], ...]:
        return tuple(
            (
                stage.name.casefold(),
                stage.display_name.casefold(),
                stage.job_count,
                stage.runs_in_parallel,
            )
            for stage in preview.stages
        )

    def _validated_identity(
        self,
        draft: PipelineProcessDraftInput,
    ) -> tuple[str, str, str, ProcessContextMode]:
        code = str(draft.code).strip().upper()
        name = str(draft.display_name).strip()
        requested_pipeline = str(draft.pipeline_code).strip()
        if not _CODE_PATTERN.fullmatch(code):
            raise ConfigurationError(
                "Process code must be 3-50 characters, begin with a letter, "
                "and contain only uppercase letters, numbers, or underscores."
            )
        if not name:
            raise ConfigurationError("Process display name is required.")
        pipelines = {
            item.code.casefold(): item
            for item in self.list_pipelines()
        }
        pipeline = pipelines.get(requested_pipeline.casefold())
        if pipeline is None:
            raise ConfigurationError(
                f"Pipeline '{requested_pipeline}' is not registered."
            )
        try:
            context_mode = ProcessContextMode(draft.context_mode)
        except ValueError as exc:
            raise ConfigurationError(
                f"Unsupported runtime context policy '{draft.context_mode}'."
            ) from exc
        return code, name, pipeline.code, context_mode

    @staticmethod
    def _definitions(
        code: str,
        name: str,
        pipeline_code: str,
        context_mode: ProcessContextMode,
        draft: PipelineProcessDraftInput,
        preview: PipelineOperationPreview,
    ) -> tuple[PlanningProcessDefinition, PlanningCycleDefinition]:
        cycle_code = f"{code}_CYCLE"
        steps: list[PlanningProcessStepDefinition] = [
            PlanningProcessStepDefinition(
                step_type=PlanningProcessStepType.PREFLIGHT,
                name="Validate Process Inputs",
                parameters={"pipelineOnly": True},
            )
        ]
        steps.append(
            PlanningProcessStepDefinition(
                step_type=PlanningProcessStepType.RUN_PIPELINE,
                name="Run Oracle Pipeline",
                parameters={
                    "pipelineStages": [
                        {
                            "name": stage.name,
                            "displayName": stage.display_name,
                            "jobCount": stage.job_count,
                            "runsInParallel": stage.runs_in_parallel,
                        }
                        for stage in preview.stages
                    ],
                    "runtimeVariableNames": [
                        variable.name for variable in preview.variables
                    ],
                },
            )
        )
        return (
            PlanningProcessDefinition(
                code=code,
                display_name=name,
                cycle_code=cycle_code,
                steps=tuple(steps),
                context_mode=context_mode,
            ),
            PlanningCycleDefinition(
                code=cycle_code,
                display_name=f"{name} Cycle",
                pipeline_code=pipeline_code,
                data_map_name=None,
            ),
        )
