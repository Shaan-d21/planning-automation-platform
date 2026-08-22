"""Validated web request models for Planning process execution."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.application.operations import (
    BusinessRuleOperationInput,
    CubeRefreshOperationInput,
    DataImportOperationInput,
    DataIntegrationOperationInput,
    DataMapOperationInput,
    MetadataImportOperationInput,
    PipelineOperationInput,
)
from app.application.planning_process import PlanningProcessInput
from app.application.process_designer import (
    PipelineProcessDraftInput,
    PipelineRunProfileInput,
)
from app.application.substitution_variables import (
    SubstitutionVariableAction,
    SubstitutionVariableOperationInput,
)
from app.application.user_variables import UserVariableOperationInput
from app.application.reports import ReportGenerationOperationInput
from app.application.data_review import (
    DataReviewAxisSelection,
    DataReviewSliceSelection,
)
from app.models.planning_process import ProcessContextMode
from app.models.data_validation import DataQualityRules
from app.models.substitution_variable import (
    RequestedSubstitutionVariableUpdate,
)
from app.models.report import (
    DataSliceReportDefinition,
    ReportAxisSegment,
)
from app.models.process_schedule import (
    ProcessScheduleInput,
    ScheduleContextMode,
    ScheduleFrequency,
)
from app.models.access_control import RoleCode


class BootstrapAdministratorRequest(BaseModel):
    """One-time first administrator setup."""

    username: str = Field(min_length=3, max_length=80)
    display_name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    password: str = Field(min_length=12, max_length=256)
    password_confirmation: str = Field(min_length=12, max_length=256)

    @field_validator("username", "display_name", "email", mode="before")
    @classmethod
    def normalize_bootstrap_text(cls, value):
        if value is None:
            return None
        return str(value).strip()

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.password_confirmation:
            raise ValueError("Password confirmation does not match.")
        return self


class PlatformLoginRequest(BaseModel):
    """Platform credentials supplied only to the authentication endpoint."""

    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_login_username(cls, value) -> str:
        return str(value).strip()


class AgentMessageRequest(BaseModel):
    """One user message sent to a user-owned agent conversation."""

    content: str = Field(min_length=1, max_length=4_000)

    @field_validator("content", mode="before")
    @classmethod
    def normalize_agent_content(cls, value) -> str:
        return str(value).strip()


class AgentActionDraftInputsRequest(BaseModel):
    """Structured values saved against a non-executable action draft."""

    inputs: dict[str, object] = Field(default_factory=dict)


class AgentApprovalDecisionRequest(BaseModel):
    """Explicit human decision for one durable LangGraph interrupt."""

    request_id: str = Field(min_length=1, max_length=200)
    decision: Literal["approve", "reject"]


class AgentClarificationResponseRequest(BaseModel):
    """Selected Oracle artifact, or null when cancelling the proposal."""

    request_id: str = Field(min_length=1, max_length=200)
    value: str | None = Field(default=None, max_length=500)


class AgentArtifactCatalogRecoveryRequest(BaseModel):
    """Pending agent choice whose Oracle catalog should be synchronized."""

    request_id: str = Field(min_length=1, max_length=200)


class AgentArtifactRegistrationRequest(BaseModel):
    """Exact Pipeline code or Data Integration name to register."""

    request_id: str = Field(min_length=1, max_length=200)
    identifier: str = Field(min_length=1, max_length=500)

    @field_validator("request_id", "identifier", mode="before")
    @classmethod
    def normalize_registration_text(cls, value) -> str:
        return str(value).strip()


class AgentInputResponseRequest(BaseModel):
    """Structured guided-operation values, or null when cancelling."""

    request_id: str = Field(min_length=1, max_length=200)
    values: dict[str, object] | None = None


class PlatformUserRequest(BaseModel):
    """Create one active platform user."""

    username: str = Field(min_length=3, max_length=80)
    display_name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    password: str = Field(min_length=12, max_length=256)
    roles: tuple[RoleCode, ...] = Field(min_length=1)

    @field_validator("username", "display_name", "email", mode="before")
    @classmethod
    def normalize_user_text(cls, value):
        if value is None:
            return None
        return str(value).strip()


class PlatformUserUpdateRequest(BaseModel):
    """Editable profile, status, and role assignments."""

    display_name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    active: bool = True
    roles: tuple[RoleCode, ...] = Field(min_length=1)

    @field_validator("display_name", "email", mode="before")
    @classmethod
    def normalize_user_update_text(cls, value):
        if value is None:
            return None
        return str(value).strip()


class PlatformPasswordResetRequest(BaseModel):
    """Administrator-controlled platform password replacement."""

    password: str = Field(min_length=12, max_length=256)
    password_confirmation: str = Field(min_length=12, max_length=256)

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.password_confirmation:
            raise ValueError("Password confirmation does not match.")
        return self


class PlanningProcessVariableUpdateRequest(BaseModel):
    """One selected, concurrency-protected variable change."""

    scope: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=80)
    expected_current_value: str = Field(max_length=255)
    new_value: str = Field(min_length=1, max_length=255)

    @field_validator("scope", "name", "new_value", mode="before")
    @classmethod
    def normalize_required_text(cls, value) -> str:
        return str(value).strip()

    @field_validator("expected_current_value", mode="before")
    @classmethod
    def normalize_expected_value(cls, value) -> str:
        return "" if value is None else str(value)

    def to_domain(self) -> RequestedSubstitutionVariableUpdate:
        return RequestedSubstitutionVariableUpdate(
            scope=self.scope,
            name=self.name,
            expected_current_value=self.expected_current_value,
            new_value=self.new_value,
        )


class ProcessScheduleRequest(BaseModel):
    """Validated schedule creation or replacement request."""

    name: str = Field(min_length=1, max_length=120)
    process_code: str = Field(min_length=1, max_length=50)
    frequency: ScheduleFrequency
    timezone: str = Field(min_length=1, max_length=120)
    first_run_local: datetime
    context_mode: ScheduleContextMode
    preset_id: int | None = Field(default=None, gt=0)
    enabled: bool = True

    @field_validator("name", "process_code", "timezone", mode="before")
    @classmethod
    def normalize_schedule_text(cls, value) -> str:
        return str(value).strip()

    @model_validator(mode="after")
    def validate_schedule_context(self):
        if self.first_run_local.tzinfo is not None:
            raise ValueError(
                "First run must be a local date and time without an offset."
            )
        if (
            self.context_mode is ScheduleContextMode.RUN_PRESET
            and self.preset_id is None
        ):
            raise ValueError("Select a saved run preset.")
        return self

    def to_domain(self) -> ProcessScheduleInput:
        return ProcessScheduleInput(
            name=self.name,
            process_code=self.process_code,
            frequency=self.frequency,
            timezone=self.timezone,
            first_run_local=self.first_run_local,
            context_mode=self.context_mode,
            preset_id=self.preset_id,
            enabled=self.enabled,
        )


class ProcessScheduleEnabledRequest(BaseModel):
    """Pause or resume one saved schedule."""

    enabled: bool


class PlanningProcessRunRequest(BaseModel):
    """Browser-supplied values for preflight and execution."""

    year: str = Field(default="", max_length=40)
    start_period: str = Field(default="", max_length=80)
    end_period: str = Field(default="", max_length=80)
    scenario: str | None = Field(default=None, max_length=120)
    version: str | None = Field(default=None, max_length=120)
    run_data_map: bool | None = None
    clear_target: bool = False
    run_refresh: bool | None = None
    run_report: bool | None = None
    pipeline_variables: dict[str, str] = Field(default_factory=dict)
    pipeline_uploads: dict[str, str] = Field(default_factory=dict)
    pipeline_inbox_files: dict[str, str] = Field(default_factory=dict)
    substitution_variable_updates: tuple[
        PlanningProcessVariableUpdateRequest, ...
    ] = ()

    @field_validator(
        "year",
        "start_period",
        "end_period",
        "scenario",
        "version",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value):
        if value is None:
            return None
        return str(value).strip()

    @field_validator(
        "pipeline_variables",
        "pipeline_uploads",
        "pipeline_inbox_files",
    )
    @classmethod
    def normalize_mappings(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        seen: set[str] = set()
        for raw_name, raw_value in value.items():
            name = str(raw_name).strip()
            item_value = str(raw_value).strip()
            if not name or not item_value:
                raise ValueError(
                    "Pipeline input names and values cannot be empty."
                )
            key = name.casefold()
            if key in seen:
                raise ValueError(
                    f"Pipeline input '{name}' was supplied more than once."
                )
            seen.add(key)
            result[name] = item_value
        return result

    @field_validator("substitution_variable_updates")
    @classmethod
    def validate_unique_variable_updates(
        cls,
        value: tuple[PlanningProcessVariableUpdateRequest, ...],
    ) -> tuple[PlanningProcessVariableUpdateRequest, ...]:
        seen: set[tuple[str, str]] = set()
        for item in value:
            key = (item.scope.casefold(), item.name.casefold())
            if key in seen:
                raise ValueError(
                    f"Substitution variable '{item.scope}.{item.name}' "
                    "was selected more than once."
                )
            seen.add(key)
        return value

    def to_domain(
        self,
        process_code: str,
        *,
        upload_paths: dict[str, Path] | None = None,
    ) -> PlanningProcessInput:
        """Convert validated transport values to the application model."""
        return PlanningProcessInput(
            process_code=str(process_code).strip(),
            year=self.year,
            start_period=self.start_period,
            end_period=self.end_period,
            scenario=self.scenario,
            version=self.version,
            run_data_map=self.run_data_map,
            clear_target=self.clear_target,
            run_refresh=self.run_refresh,
            run_report=self.run_report,
            pipeline_variables=self.pipeline_variables,
            pipeline_uploads=upload_paths or {},
            pipeline_inbox_files=self.pipeline_inbox_files,
            variable_updates=tuple(
                item.to_domain()
                for item in self.substitution_variable_updates
            ),
        )


class PipelineProcessDraftRequest(BaseModel):
    """Validated request for publishing one Oracle Pipeline."""

    code: str = Field(min_length=3, max_length=50)
    display_name: str = Field(min_length=1, max_length=160)
    pipeline_code: str = Field(min_length=1, max_length=50)
    context_mode: ProcessContextMode = ProcessContextMode.PROMPT_EACH_RUN

    @field_validator(
        "code",
        "display_name",
        "pipeline_code",
        mode="before",
    )
    @classmethod
    def normalize_designer_text(cls, value) -> str:
        return str(value).strip()

    def to_domain(self) -> PipelineProcessDraftInput:
        """Convert transport values to a designer use-case input."""
        return PipelineProcessDraftInput(
            code=self.code,
            display_name=self.display_name,
            pipeline_code=self.pipeline_code,
            context_mode=self.context_mode,
        )


class PipelineRunProfileRequest(BaseModel):
    """Optional reusable Pipeline values and file strategies."""

    name: str = Field(min_length=1, max_length=120)
    year: str = Field(default="", max_length=40)
    start_period: str = Field(default="", max_length=80)
    end_period: str = Field(default="", max_length=80)
    scenario: str | None = Field(default=None, max_length=120)
    version: str | None = Field(default=None, max_length=120)
    pipeline_variables: dict[str, str] = Field(default_factory=dict)
    inbox_files: dict[str, str] = Field(default_factory=dict)
    required_upload_keys: tuple[str, ...] = ()

    @field_validator(
        "name",
        "year",
        "start_period",
        "end_period",
        "scenario",
        "version",
        mode="before",
    )
    @classmethod
    def normalize_profile_text(cls, value):
        if value is None:
            return None
        return str(value).strip()

    @field_validator("pipeline_variables", "inbox_files")
    @classmethod
    def normalize_profile_mappings(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        return _normalize_name_value_mapping(
            value,
            label="Run preset input",
        )

    @field_validator("required_upload_keys")
    @classmethod
    def normalize_upload_keys(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                str(item).strip()
                for item in value
                if str(item).strip()
            )
        )

    def to_domain(self) -> PipelineRunProfileInput:
        return PipelineRunProfileInput(
            name=self.name,
            year=self.year,
            start_period=self.start_period,
            end_period=self.end_period,
            scenario=self.scenario,
            version=self.version,
            pipeline_variables=self.pipeline_variables,
            inbox_files=self.inbox_files,
            required_upload_keys=self.required_upload_keys,
        )


class PipelineRunProfileExecutionRequest(BaseModel):
    """Session upload tokens supplied for one preset execution."""

    uploads: dict[str, str] = Field(default_factory=dict)

    @field_validator("uploads")
    @classmethod
    def normalize_profile_uploads(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        return _normalize_name_value_mapping(
            value,
            label="Preset upload",
        )

class StandaloneFlowRecoveryRunRequest(BaseModel):
    """Explicit approval to retry a reviewed failed flow suffix."""

    failed_step_sequence: int = Field(gt=0)
    confirmation: Literal["RETRY_FROM_FAILED_STEP"]
    replacement_uploads: dict[str, str] = Field(default_factory=dict)

    @field_validator("replacement_uploads")
    @classmethod
    def normalize_replacement_uploads(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        return _normalize_name_value_mapping(
            value,
            label="Recovery upload",
        )


class BusinessRuleRunRequest(BaseModel):
    """Validated browser request for one Business Rule."""

    rule_name: str = Field(min_length=1, max_length=250)
    runtime_prompts: dict[str, str] = Field(default_factory=dict)
    planning_task_id: int | None = Field(default=None, gt=0)

    @field_validator("rule_name", mode="before")
    @classmethod
    def normalize_rule_name(cls, value) -> str:
        return str(value).strip()

    @field_validator("runtime_prompts")
    @classmethod
    def normalize_runtime_prompts(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        return _normalize_name_value_mapping(
            value,
            label="Runtime prompt",
        )

    def to_domain(self) -> BusinessRuleOperationInput:
        return BusinessRuleOperationInput(
            rule_name=self.rule_name,
            runtime_prompts=self.runtime_prompts,
        )


class DataMapRunRequest(BaseModel):
    """Validated browser request for one Data Map."""

    data_map_name: str = Field(min_length=1, max_length=250)
    clear_target: bool = False
    member_overrides: dict[str, str] = Field(default_factory=dict)
    exclusion_overrides: dict[str, str] = Field(default_factory=dict)
    planning_task_id: int | None = Field(default=None, gt=0)

    @field_validator("data_map_name", mode="before")
    @classmethod
    def normalize_data_map_name(cls, value) -> str:
        return str(value).strip()

    @field_validator("member_overrides", "exclusion_overrides")
    @classmethod
    def normalize_overrides(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        return _normalize_name_value_mapping(
            value,
            label="Data Map override",
        )

    def to_domain(self) -> DataMapOperationInput:
        return DataMapOperationInput(
            data_map_name=self.data_map_name,
            clear_target=self.clear_target,
            member_overrides=self.member_overrides,
            exclusion_overrides=self.exclusion_overrides,
        )


class PipelineRunRequest(BaseModel):
    """Validated browser request for one Pipeline."""

    pipeline_code: str = Field(min_length=1, max_length=30)
    variables: dict[str, str] = Field(default_factory=dict)
    uploads: dict[str, str] = Field(default_factory=dict)
    inbox_files: dict[str, str] = Field(default_factory=dict)
    planning_task_id: int | None = Field(default=None, gt=0)

    @field_validator("pipeline_code", mode="before")
    @classmethod
    def normalize_pipeline_code(cls, value) -> str:
        return str(value).strip()

    @field_validator("variables", "uploads", "inbox_files")
    @classmethod
    def normalize_pipeline_mappings(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        return _normalize_name_value_mapping(
            value,
            label="Pipeline input",
        )

    def to_domain(
        self,
        *,
        upload_paths: dict[str, Path] | None = None,
    ) -> PipelineOperationInput:
        return PipelineOperationInput(
            pipeline_code=self.pipeline_code,
            variables=self.variables,
            uploads=upload_paths or {},
            inbox_files=self.inbox_files,
        )


class ExcelPipelineRunRequest(BaseModel):
    """Validated Excel request using existing Oracle Inbox files only."""

    variables: dict[str, str] = Field(default_factory=dict)
    inbox_files: dict[str, str] = Field(default_factory=dict)
    confirmed: bool = False

    @field_validator("variables", "inbox_files")
    @classmethod
    def normalize_excel_mappings(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        return _normalize_name_value_mapping(
            value,
            label="Excel Pipeline input",
        )

    @model_validator(mode="after")
    def require_confirmation(self):
        if not self.confirmed:
            raise ValueError(
                "Excel must explicitly confirm the reviewed Pipeline run."
            )
        return self

    def to_domain(self, pipeline_code: str) -> PipelineOperationInput:
        return PipelineOperationInput(
            pipeline_code=pipeline_code,
            variables=self.variables,
            uploads={},
            inbox_files=self.inbox_files,
        )


class PipelineRegistrationRequest(BaseModel):
    """Exact Oracle Pipeline code to verify and register."""

    pipeline_code: str = Field(min_length=1, max_length=50)

    @field_validator("pipeline_code", mode="before")
    @classmethod
    def normalize_registration_code(cls, value) -> str:
        return str(value).strip()


class DataIntegrationRegistrationRequest(BaseModel):
    """Exact Oracle Data Integration name to register safely."""

    integration_name: str = Field(min_length=1, max_length=250)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("integration_name", mode="before")
    @classmethod
    def normalize_integration_name(cls, value) -> str:
        return str(value).strip()

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value):
        if value is None:
            return None
        return str(value).strip() or None


class DataIntegrationRunRequest(BaseModel):
    """Validated browser request for a file-based Data Integration."""

    integration_name: str = Field(min_length=1, max_length=250)
    start_period: str = Field(min_length=1, max_length=100)
    end_period: str = Field(min_length=1, max_length=100)
    import_mode: str = Field(default="Replace", max_length=40)
    export_mode: str = Field(default="Merge", max_length=40)
    upload_token: str | None = Field(default=None, max_length=64)
    inbox_file: str | None = Field(default=None, max_length=500)
    use_configured_file: bool = False
    planning_task_id: int | None = Field(default=None, gt=0)

    @field_validator(
        "integration_name",
        "start_period",
        "end_period",
        "import_mode",
        "export_mode",
        "upload_token",
        "inbox_file",
        mode="before",
    )
    @classmethod
    def normalize_integration_text(cls, value):
        if value is None:
            return None
        return str(value).strip()

    @model_validator(mode="after")
    def validate_file_source(self):
        selected_sources = sum(
            (
                bool(self.upload_token),
                bool(self.inbox_file),
                self.use_configured_file,
            )
        )
        if selected_sources != 1:
            raise ValueError(
                "Select one local upload, Oracle Inbox file, or the file "
                "configured in Oracle."
            )
        return self

    def to_domain(
        self,
        *,
        upload_path: Path | None = None,
    ) -> DataIntegrationOperationInput:
        return DataIntegrationOperationInput(
            integration_name=self.integration_name,
            start_period=self.start_period,
            end_period=self.end_period,
            import_mode=self.import_mode,
            export_mode=self.export_mode,
            upload_path=upload_path,
            inbox_file=self.inbox_file,
            use_configured_file=self.use_configured_file,
        )


class MetadataImportRunRequest(BaseModel):
    """Validated browser request for a saved Metadata Import job."""

    job_name: str = Field(min_length=1, max_length=250)
    upload_token: str | None = Field(default=None, max_length=64)
    inbox_file: str | None = Field(default=None, max_length=500)
    use_configured_file: bool = False
    error_file_name: str | None = Field(default=None, max_length=250)
    refresh_job_name: str | None = Field(default=None, max_length=250)
    planning_task_id: int | None = Field(default=None, gt=0)

    @field_validator(
        "job_name",
        "upload_token",
        "inbox_file",
        "error_file_name",
        "refresh_job_name",
        mode="before",
    )
    @classmethod
    def normalize_metadata_text(cls, value):
        if value is None:
            return None
        return str(value).strip() or None

    @model_validator(mode="after")
    def validate_metadata_file_source(self):
        selected_sources = sum(
            (
                bool(self.upload_token),
                bool(self.inbox_file),
                self.use_configured_file,
            )
        )
        if selected_sources != 1:
            raise ValueError(
                "Select one local metadata upload, Oracle Inbox file, or "
                "the file configured in Oracle."
            )
        return self

    def to_domain(
        self,
        *,
        upload_path: Path | None = None,
    ) -> MetadataImportOperationInput:
        return MetadataImportOperationInput(
            job_name=self.job_name,
            upload_path=upload_path,
            inbox_file=self.inbox_file,
            use_configured_file=self.use_configured_file,
            error_file_name=self.error_file_name,
            refresh_job_name=self.refresh_job_name,
        )


class DataImportRunRequest(BaseModel):
    """Validated browser request for a saved native Planning data job."""

    job_name: str = Field(min_length=1, max_length=250)
    upload_token: str | None = Field(default=None, max_length=64)
    inbox_file: str | None = Field(default=None, max_length=500)
    use_configured_file: bool = False
    error_file_name: str | None = Field(default=None, max_length=250)
    planning_task_id: int | None = Field(default=None, gt=0)

    @field_validator(
        "job_name",
        "upload_token",
        "inbox_file",
        "error_file_name",
        mode="before",
    )
    @classmethod
    def normalize_data_import_text(cls, value):
        if value is None:
            return None
        return str(value).strip() or None

    @model_validator(mode="after")
    def validate_data_file_source(self):
        selected_sources = sum(
            (
                bool(self.upload_token),
                bool(self.inbox_file),
                self.use_configured_file,
            )
        )
        if selected_sources != 1:
            raise ValueError(
                "Select one local data upload, Oracle Inbox file, or the "
                "file configured in Oracle."
            )
        return self

    def to_domain(
        self,
        *,
        upload_path: Path | None = None,
    ) -> DataImportOperationInput:
        return DataImportOperationInput(
            job_name=self.job_name,
            upload_path=upload_path,
            inbox_file=self.inbox_file,
            use_configured_file=self.use_configured_file,
            error_file_name=self.error_file_name,
        )


class SubstitutionVariableRunRequest(BaseModel):
    """Validated browser request for one variable change."""

    action: SubstitutionVariableAction
    scope: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=255)
    expected_current_value: str | None = Field(
        default=None,
        max_length=255,
    )
    planning_task_id: int | None = Field(default=None, gt=0)

    @field_validator(
        "scope",
        "name",
        "value",
        mode="before",
    )
    @classmethod
    def normalize_substitution_variable_text(cls, value) -> str:
        return str(value).strip()

    @model_validator(mode="after")
    def validate_safe_update(self):
        if (
            self.action is SubstitutionVariableAction.UPDATE
            and self.expected_current_value is None
        ):
            raise ValueError(
                "The current value is required for a safe variable update."
            )
        return self

    def to_domain(self) -> SubstitutionVariableOperationInput:
        return SubstitutionVariableOperationInput(
            action=self.action,
            scope=self.scope,
            name=self.name,
            value=self.value,
            expected_current_value=self.expected_current_value,
        )


class UserVariableRunRequest(BaseModel):
    """Validated browser request for one user-variable value assignment."""

    user_name: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    dimension: str = Field(min_length=1, max_length=255)
    member: str = Field(min_length=1, max_length=255)
    expected_current_member: str | None = Field(default=None, max_length=255)
    planning_task_id: int | None = Field(default=None, gt=0)

    @field_validator("user_name", "name", "dimension", "member", mode="before")
    @classmethod
    def normalize_user_variable_text(cls, value) -> str:
        return str(value).strip()

    def to_domain(self) -> UserVariableOperationInput:
        return UserVariableOperationInput(
            user_name=self.user_name,
            name=self.name,
            dimension=self.dimension,
            member=self.member,
            expected_current_member=self.expected_current_member,
        )


class CubeRefreshRunRequest(BaseModel):
    """Validated browser request for one saved Cube Refresh job."""

    job_name: str = Field(min_length=1, max_length=250)
    planning_task_id: int | None = Field(default=None, gt=0)

    @field_validator("job_name", mode="before")
    @classmethod
    def normalize_cube_refresh_job(cls, value) -> str:
        return str(value).strip()

    def to_domain(self) -> CubeRefreshOperationInput:
        return CubeRefreshOperationInput(job_name=self.job_name)


class ReportPreflightRequest(BaseModel):
    """Validated request to inspect one report or Planning form."""

    form_name: str = Field(min_length=1, max_length=250)

    @field_validator("form_name", mode="before")
    @classmethod
    def normalize_report_preflight_name(cls, value) -> str:
        return str(value).strip()


class DataReviewAxisRequest(BaseModel):
    """One dimension placed on a Data Review row or column axis."""

    dimension: str = Field(min_length=1, max_length=250)
    members: tuple[str, ...] = Field(min_length=1, max_length=1000)

    @field_validator("dimension", mode="before")
    @classmethod
    def normalize_data_review_dimension(cls, value) -> str:
        return str(value).strip()

    @field_validator("members")
    @classmethod
    def normalize_data_review_members(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in values
                if str(value).strip()
            )
        )
        if not normalized:
            raise ValueError("At least one member is required.")
        return normalized

    def to_domain(self) -> DataReviewAxisSelection:
        return DataReviewAxisSelection(
            dimension=self.dimension,
            members=self.members,
        )


class DataReviewSliceRequest(BaseModel):
    """Validated ad-hoc Planning cube slice."""

    cube: str = Field(min_length=1, max_length=250)
    pov: dict[str, str] = Field(default_factory=dict, max_length=30)
    rows: tuple[DataReviewAxisRequest, ...] = Field(
        min_length=1,
        max_length=20,
    )
    columns: tuple[DataReviewAxisRequest, ...] = Field(
        min_length=1,
        max_length=20,
    )

    @field_validator("cube", mode="before")
    @classmethod
    def normalize_data_review_cube(cls, value) -> str:
        return str(value).strip()

    @field_validator("pov")
    @classmethod
    def normalize_data_review_pov(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        return _normalize_name_value_mapping(
            value,
            label="Data Review POV",
        )

    def to_domain(self) -> DataReviewSliceSelection:
        return DataReviewSliceSelection(
            cube=self.cube,
            pov=self.pov,
            rows=tuple(item.to_domain() for item in self.rows),
            columns=tuple(item.to_domain() for item in self.columns),
        )


class DataQualityRulesRequest(BaseModel):
    """Bounded checks requested for one live Planning data slice."""

    check_missing: bool = True
    check_zero: bool = False
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    max_issues: int = Field(default=500, ge=1, le=2000)

    @model_validator(mode="after")
    def validate_range(self):
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("Validation minimum cannot exceed maximum.")
        return self

    def to_domain(self) -> DataQualityRules:
        return DataQualityRules(
            check_missing=self.check_missing,
            check_zero=self.check_zero,
            minimum=self.minimum,
            maximum=self.maximum,
            max_issues=self.max_issues,
        )


class DataReviewValidationRequest(BaseModel):
    """One live Planning slice and its requested data-quality checks."""

    slice: DataReviewSliceRequest
    rules: DataQualityRulesRequest = Field(
        default_factory=DataQualityRulesRequest
    )
    planning_task_id: int | None = Field(default=None, gt=0)


class DataReviewComparisonRequest(BaseModel):
    """Validated source-to-target form reconciliation request."""

    source: DataReviewSliceRequest
    target: DataReviewSliceRequest
    tolerance: Decimal = Field(default=Decimal("0"), ge=0)
    max_mismatches: int = Field(default=100, ge=1, le=500)
    include_cells: bool = True
    planning_task_id: int | None = Field(default=None, gt=0)


class ReportAxisDimensionRequest(BaseModel):
    """One dimension and its selected members on a report axis."""

    dimension: str = Field(min_length=1, max_length=250)
    members: list[str] = Field(min_length=1, max_length=1000)

    @field_validator("dimension", mode="before")
    @classmethod
    def normalize_dimension(cls, value) -> str:
        return str(value).strip()

    @field_validator("members")
    @classmethod
    def normalize_members(cls, values: list[str]) -> list[str]:
        normalized = [str(value).strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("Report axis members cannot be empty.")
        keys = [value.casefold() for value in normalized]
        if len(keys) != len(set(keys)):
            raise ValueError(
                "Report axis members cannot contain duplicates."
            )
        return normalized


class ReportRegistrationRequest(BaseModel):
    """Validated browser request for one data-slice report definition."""

    name: str = Field(min_length=1, max_length=250)
    title: str = Field(min_length=1, max_length=250)
    cube: str = Field(min_length=1, max_length=250)
    pov: dict[str, str] = Field(default_factory=dict)
    columns: list[ReportAxisDimensionRequest] = Field(
        min_length=1,
        max_length=20,
    )
    rows: list[ReportAxisDimensionRequest] = Field(
        min_length=1,
        max_length=20,
    )

    @field_validator("name", "title", "cube", mode="before")
    @classmethod
    def normalize_registration_text(cls, value) -> str:
        return str(value).strip()

    @field_validator("pov")
    @classmethod
    def normalize_registration_pov(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        return _normalize_name_value_mapping(
            value,
            label="Report POV",
        )

    @model_validator(mode="after")
    def validate_unique_dimensions(self):
        dimensions = [
            *self.pov.keys(),
            *(item.dimension for item in self.columns),
            *(item.dimension for item in self.rows),
        ]
        normalized = [item.casefold() for item in dimensions]
        if len(normalized) != len(set(normalized)):
            raise ValueError(
                "Each dimension can belong to only one report axis."
            )
        return self

    def to_domain(self) -> DataSliceReportDefinition:
        """Convert validated transport values to the catalog model."""
        return DataSliceReportDefinition(
            name=self.name,
            title=self.title,
            cube=self.cube,
            pov=tuple(self.pov.items()),
            columns=(
                ReportAxisSegment(
                    dimensions=tuple(
                        item.dimension for item in self.columns
                    ),
                    members=tuple(
                        tuple(item.members) for item in self.columns
                    ),
                ),
            ),
            rows=(
                ReportAxisSegment(
                    dimensions=tuple(item.dimension for item in self.rows),
                    members=tuple(
                        tuple(item.members) for item in self.rows
                    ),
                ),
            ),
        )


class ReportRunRequest(BaseModel):
    """Validated browser request for one Excel report."""

    form_name: str = Field(min_length=1, max_length=250)
    title: str = Field(min_length=1, max_length=250)
    page_member_overrides: dict[str, str] = Field(default_factory=dict)

    @field_validator("form_name", "title", mode="before")
    @classmethod
    def normalize_report_text(cls, value) -> str:
        return str(value).strip()

    @field_validator("page_member_overrides")
    @classmethod
    def normalize_report_pov(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        return _normalize_name_value_mapping(
            value,
            label="Report POV",
        )

    def to_domain(self) -> ReportGenerationOperationInput:
        return ReportGenerationOperationInput(
            form_name=self.form_name,
            title=self.title,
            page_member_overrides=tuple(
                self.page_member_overrides.items()
            ),
        )


def _normalize_name_value_mapping(
    values: dict[str, str],
    *,
    label: str,
) -> dict[str, str]:
    result: dict[str, str] = {}
    seen: set[str] = set()
    for raw_name, raw_value in values.items():
        name = str(raw_name).strip()
        value = str(raw_value).strip()
        if not name or not value:
            raise ValueError(f"{label} names and values cannot be empty.")
        key = name.casefold()
        if key in seen:
            raise ValueError(f"{label} '{name}' was supplied more than once.")
        seen.add(key)
        result[name] = value
    return result
