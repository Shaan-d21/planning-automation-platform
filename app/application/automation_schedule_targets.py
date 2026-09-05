"""Governed target adapters and dispatch for automation schedules."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.application.automation_scheduling import (
    AutomationScheduleApplicationService,
)
from app.application.operation_execution_manager import (
    OperationExecutionManager,
)
from app.application.operations import (
    OperationCatalogService,
    PipelineOperationInput,
    PipelineOperationPreview,
)
from app.config.settings import Settings
from app.models.access_control import ExecutionActor, TriggerSource
from app.models.automation_schedule import (
    AutomationInputPolicy,
    AutomationSchedule,
    AutomationScheduleInput,
    AutomationSchedulePreview,
    AutomationScheduleRun,
    AutomationTargetType,
)
from app.models.oracle_artifact import OracleEnvironment
from app.models.notification import TaskNotificationEvent, TaskNotificationStatus
from app.models.business_rule_rtp import RTPRegistryImportResult
from app.services.notification_service import NotificationService
from app.services.business_rule_rtp_registry import (
    BusinessRuleRTPRegistryService,
)
from app.services.calc_manager_export_service import CalcManagerExportService
from app.utils.exceptions import (
    AutomationScheduleError,
    EPMError,
    ExecutionQueueConflictError,
    OperationError,
)


class AutomationScheduleTargetAdapter(Protocol):
    """Target-specific live validation and operation resolution boundary."""

    target_type: AutomationTargetType

    def operation_for(
        self,
        schedule: AutomationSchedule | AutomationScheduleInput,
    ) -> object:
        """Return one fully governed input for the existing operation layer."""


@dataclass(frozen=True, slots=True)
class ScheduledDispatchResult:
    """Outcome of dispatching one claimed schedule occurrence."""

    run_id: int
    schedule_id: int
    target_type: AutomationTargetType
    target_key: str
    status: str
    execution_id: str | None
    message: str


@dataclass(frozen=True, slots=True)
class RTPRegistrySyncInput:
    """Validated export settings for one unattended RTP registry refresh."""

    snapshot_prefix: str


class PipelineScheduleTargetAdapter:
    """Resolve an unattended Pipeline using its current Oracle definition."""

    target_type = AutomationTargetType.ORACLE_PIPELINE
    _CONFIGURATION_KEYS = frozenset({"variables", "inbox_files"})
    _LOCAL_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")

    def __init__(
        self,
        settings: Settings,
        *,
        catalog: OperationCatalogService | None = None,
    ) -> None:
        self._settings = settings
        self._catalog = catalog or OperationCatalogService(settings)
        self._environment = OracleEnvironment.from_settings(
            settings.epm_base_url,
            settings.application_name,
        )

    def operation_for(
        self,
        schedule: AutomationSchedule | AutomationScheduleInput,
    ) -> PipelineOperationInput:
        """Live-validate variables and Inbox references for one Pipeline."""
        if schedule.target_type is not self.target_type:
            raise AutomationScheduleError(
                "The Pipeline schedule adapter received an unsupported target."
            )
        if schedule.environment_key != self._environment.key:
            raise AutomationScheduleError(
                "This schedule belongs to a different Oracle EPM environment."
            )
        if schedule.input_policy is AutomationInputPolicy.DYNAMIC:
            raise AutomationScheduleError(
                "Dynamic Pipeline inputs are not enabled yet. Use Oracle "
                "defaults or fixed unattended values."
            )

        configuration = dict(schedule.configuration)
        unknown_sections = set(configuration) - self._CONFIGURATION_KEYS
        if unknown_sections:
            raise AutomationScheduleError(
                "Unsupported Pipeline schedule configuration: "
                + ", ".join(sorted(unknown_sections))
            )
        variables = self._string_mapping(
            configuration.get("variables"),
            label="Pipeline variables",
        )
        inbox_files = self._string_mapping(
            configuration.get("inbox_files"),
            label="Pipeline Inbox files",
        )
        if (
            schedule.input_policy is AutomationInputPolicy.ORACLE_DEFAULTS
            and (variables or inbox_files)
        ):
            raise AutomationScheduleError(
                "Oracle-default schedules cannot contain fixed Pipeline "
                "variables or Inbox files. Select the fixed input policy."
            )

        try:
            preview = self._catalog.preflight_pipeline(schedule.target_key)
        except EPMError as exc:
            raise AutomationScheduleError(
                f"Pipeline '{schedule.target_key}' could not be validated "
                f"against Oracle: {exc}"
            ) from exc
        self._validate_variables(preview, variables)
        self._validate_files(preview, inbox_files)
        return PipelineOperationInput(
            pipeline_code=preview.code,
            variables=variables,
            uploads={},
            inbox_files=inbox_files,
        )

    @classmethod
    def _validate_variables(
        cls,
        preview: PipelineOperationPreview,
        supplied: Mapping[str, str],
    ) -> None:
        supplied_by_name = {name.casefold(): value for name, value in supplied.items()}
        known = {variable.name.casefold(): variable for variable in preview.variables}
        unknown = set(supplied_by_name) - set(known)
        if unknown:
            raise AutomationScheduleError(
                "Pipeline variable(s) do not match the current Oracle "
                "definition: " + ", ".join(sorted(unknown))
            )
        missing = [
            variable.display_name
            for key, variable in known.items()
            if variable.required
            and not supplied_by_name.get(key)
            and not variable.default_value
        ]
        if missing:
            raise AutomationScheduleError(
                "Unattended Pipeline execution requires values for: "
                + ", ".join(missing)
            )

    @classmethod
    def _validate_files(
        cls,
        preview: PipelineOperationPreview,
        supplied: Mapping[str, str],
    ) -> None:
        supplied_by_key = {name.casefold(): value for name, value in supplied.items()}
        known = {
            requirement.key.casefold(): requirement
            for requirement in preview.file_requirements
        }
        unknown = set(supplied_by_key) - set(known)
        if unknown:
            raise AutomationScheduleError(
                "Pipeline file input(s) do not match the current Oracle "
                "definition: " + ", ".join(sorted(unknown))
            )
        for key, reference in supplied_by_key.items():
            if cls._LOCAL_PATH.match(reference):
                raise AutomationScheduleError(
                    f"Pipeline file input '{known[key].display_name}' must "
                    "reference an unattended Oracle Inbox file, not a local "
                    "computer path."
                )
        missing = [
            requirement.display_name
            for key, requirement in known.items()
            if requirement.required
            and not supplied_by_key.get(key)
            and not requirement.configured_reference
        ]
        if missing:
            raise AutomationScheduleError(
                "Unattended Pipeline execution requires Oracle Inbox files "
                "for: " + ", ".join(missing)
            )

    @staticmethod
    def _string_mapping(value: object, *, label: str) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise AutomationScheduleError(f"{label} must be a JSON object.")
        result: dict[str, str] = {}
        seen: set[str] = set()
        for raw_name, raw_value in value.items():
            name = str(raw_name).strip()
            resolved = str(raw_value).strip()
            if not name or not resolved:
                raise AutomationScheduleError(
                    f"{label} cannot contain empty names or values."
                )
            normalized = name.casefold()
            if normalized in seen:
                raise AutomationScheduleError(
                    f"{label} contains '{name}' more than once."
                )
            seen.add(normalized)
            result[name] = resolved
        return result


class RTPRegistrySyncScheduleTargetAdapter:
    """Generate and import a fresh Calculation Manager LCM snapshot."""

    target_type = AutomationTargetType.RTP_REGISTRY_SYNC

    def __init__(
        self,
        settings: Settings,
        *,
        registry: BusinessRuleRTPRegistryService | None = None,
        exporter: CalcManagerExportService | None = None,
    ) -> None:
        self._registry = registry or BusinessRuleRTPRegistryService(settings)
        self._exporter = exporter or CalcManagerExportService(settings)
        self._environment = OracleEnvironment.from_settings(
            settings.epm_base_url,
            settings.application_name,
        )

    def operation_for(
        self,
        schedule: AutomationSchedule | AutomationScheduleInput,
    ) -> RTPRegistrySyncInput:
        """Confirm Oracle can create Calculation Manager snapshots itself."""
        if schedule.target_type is not self.target_type:
            raise AutomationScheduleError(
                "The RTP registry schedule adapter received an unsupported "
                "target."
            )
        if schedule.environment_key != self._environment.key:
            raise AutomationScheduleError(
                "This schedule belongs to a different Oracle EPM environment."
            )
        if schedule.input_policy is not AutomationInputPolicy.FIXED:
            raise AutomationScheduleError(
                "RTP registry synchronization requires fixed automated "
                "snapshot settings."
            )
        if schedule.configuration:
            raise AutomationScheduleError(
                "RTP registry schedules cannot contain Pipeline inputs."
            )
        snapshot_prefix = self._exporter.normalize_snapshot_prefix(
            schedule.target_key
        )
        self._exporter.ensure_supported()
        return RTPRegistrySyncInput(snapshot_prefix=snapshot_prefix)

    def execute(
        self,
        operation: RTPRegistrySyncInput,
    ) -> RTPRegistryImportResult:
        """Export, download, parse, publish, and clean up without user work."""
        snapshot = self._exporter.generate(operation.snapshot_prefix)
        try:
            return self._registry.import_package(
                f"{snapshot.snapshot_name}.zip",
                snapshot.content,
            )
        finally:
            self._exporter.delete(snapshot.snapshot_name)


class AutomationScheduleCoordinator:
    """Validate schedule definitions and dispatch their due occurrences."""

    def __init__(
        self,
        schedule_service: AutomationScheduleApplicationService,
        execution_manager: OperationExecutionManager,
        adapters: tuple[AutomationScheduleTargetAdapter, ...],
        *,
        notification_service: NotificationService | None = None,
        environment_url: str = "",
        application_name: str = "",
        logger: logging.Logger | None = None,
    ) -> None:
        self._schedules = schedule_service
        self._execution_manager = execution_manager
        self._adapters = {adapter.target_type: adapter for adapter in adapters}
        self._notification_service = notification_service
        self._environment_url = environment_url
        self._application_name = application_name
        self._logger = logger or logging.getLogger(__name__)

    def preview(
        self,
        schedule_input: AutomationScheduleInput,
        *,
        now: datetime | None = None,
    ) -> AutomationSchedulePreview:
        self._adapter(schedule_input.target_type).operation_for(schedule_input)
        return self._schedules.preview(schedule_input, now=now)

    def create(
        self,
        schedule_input: AutomationScheduleInput,
        *,
        now: datetime | None = None,
    ) -> AutomationSchedule:
        self._adapter(schedule_input.target_type).operation_for(schedule_input)
        return self._schedules.create(schedule_input, now=now)

    def update(
        self,
        schedule_id: int,
        schedule_input: AutomationScheduleInput,
        *,
        now: datetime | None = None,
    ) -> AutomationSchedule:
        self._adapter(schedule_input.target_type).operation_for(schedule_input)
        return self._schedules.update(schedule_id, schedule_input, now=now)

    def set_enabled(
        self,
        schedule_id: int,
        enabled: bool,
        *,
        now: datetime | None = None,
    ) -> AutomationSchedule:
        if enabled:
            schedule = self._schedules.get(schedule_id)
            self._adapter(schedule.target_type).operation_for(schedule)
        return self._schedules.set_enabled(schedule_id, enabled, now=now)

    def dispatch_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> tuple[ScheduledDispatchResult, ...]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        results: list[ScheduledDispatchResult] = []
        for run in self._schedules.claim_due(now=current, limit=limit):
            results.append(self._dispatch(run, now=current))
        return tuple(results)

    def _dispatch(
        self,
        run: AutomationScheduleRun,
        *,
        now: datetime,
    ) -> ScheduledDispatchResult:
        schedule = self._schedules.get(run.schedule_id)
        try:
            adapter = self._adapter(schedule.target_type)
            operation_input = adapter.operation_for(schedule)
            if isinstance(operation_input, PipelineOperationInput):
                execution = self._execution_manager.submit(
                    operation_input,
                    actor=ExecutionActor(
                        username="scheduler",
                        display_name="Automation Scheduler",
                        trigger_source=TriggerSource.SCHEDULED,
                    ),
                )
                self._schedules.record_submitted(
                    run.run_id,
                    execution.execution_id,
                    now=now,
                )
                return self._submitted_result(schedule, run, execution.execution_id)
            if (
                isinstance(operation_input, RTPRegistrySyncInput)
                and isinstance(adapter, RTPRegistrySyncScheduleTargetAdapter)
            ):
                result = adapter.execute(operation_input)
                self._schedules.record_completed(run.run_id, now=now)
                message = (
                    f"RTP registry synchronized {result.rules_imported} rule(s) "
                    f"and {result.prompts_imported} runtime prompt(s)."
                )
                self._notify_handoff(schedule, "COMPLETED", message, now=now)
                self._logger.info(
                    "Scheduled RTP registry sync completed: schedule_id=%s, "
                    "source='%s', sync_run_id=%s.",
                    schedule.schedule_id,
                    schedule.target_key,
                    result.sync_run_id,
                )
                return ScheduledDispatchResult(
                    run_id=run.run_id,
                    schedule_id=schedule.schedule_id,
                    target_type=schedule.target_type,
                    target_key=schedule.target_key,
                    status="COMPLETED",
                    execution_id=None,
                    message=message,
                )
            else:
                raise AutomationScheduleError(
                    "The scheduled target did not resolve to a supported "
                    "operation input."
                )
        except OperationError as exc:
            message = " ".join(str(exc).split())[:1_500]
            if isinstance(exc.__cause__, ExecutionQueueConflictError) or (
                "active execution" in message.casefold()
            ):
                self._schedules.record_skipped(run.run_id, message, now=now)
                status = "SKIPPED"
            else:
                self._schedules.record_failed(run.run_id, message, now=now)
                status = "FAILED"
            self._notify_handoff(schedule, status, message, now=now)
            self._logger.warning(
                "Scheduled target was not submitted: schedule_id=%s, %s",
                schedule.schedule_id,
                message,
            )
            return ScheduledDispatchResult(
                run_id=run.run_id,
                schedule_id=schedule.schedule_id,
                target_type=schedule.target_type,
                target_key=schedule.target_key,
                status=status,
                execution_id=None,
                message=message,
            )
        except EPMError as exc:
            message = " ".join(str(exc).split())[:1_500]
            self._schedules.record_failed(run.run_id, message, now=now)
            self._notify_handoff(schedule, "FAILED", message, now=now)
            self._logger.warning(
                "Scheduled target validation failed: schedule_id=%s, %s",
                schedule.schedule_id,
                message,
            )
            return ScheduledDispatchResult(
                run_id=run.run_id,
                schedule_id=schedule.schedule_id,
                target_type=schedule.target_type,
                target_key=schedule.target_key,
                status="FAILED",
                execution_id=None,
                message=message,
            )
        except Exception as exc:
            message = "Unexpected scheduling failure. Review the server log."
            self._schedules.record_failed(run.run_id, message, now=now)
            self._notify_handoff(schedule, "FAILED", message, now=now)
            self._logger.exception(
                "Unexpected scheduled target submission failure: "
                "schedule_id=%s.",
                schedule.schedule_id,
            )
            return ScheduledDispatchResult(
                run_id=run.run_id,
                schedule_id=schedule.schedule_id,
                target_type=schedule.target_type,
                target_key=schedule.target_key,
                status="FAILED",
                execution_id=None,
                message=message,
            )

    def _submitted_result(
        self,
        schedule: AutomationSchedule,
        run: AutomationScheduleRun,
        execution_id: str,
    ) -> ScheduledDispatchResult:
        self._logger.info(
            "Scheduled Pipeline submitted: schedule_id=%s, pipeline='%s', "
            "execution_id='%s'.",
            schedule.schedule_id,
            schedule.target_key,
            execution_id,
        )
        return ScheduledDispatchResult(
            run_id=run.run_id,
            schedule_id=schedule.schedule_id,
            target_type=schedule.target_type,
            target_key=schedule.target_key,
            status="SUBMITTED",
            execution_id=execution_id,
            message="Scheduled Pipeline was submitted successfully.",
        )

    def _notify_handoff(
        self,
        schedule: AutomationSchedule,
        status: str,
        message: str,
        *,
        now: datetime,
    ) -> None:
        """Notify only when no normal operation execution was submitted."""
        if self._notification_service is None:
            return
        notification_status = {
            "COMPLETED": TaskNotificationStatus.SUCCESS,
            "SKIPPED": TaskNotificationStatus.SKIPPED,
        }.get(status, TaskNotificationStatus.FAILED)
        self._notification_service.publish(
            TaskNotificationEvent(
                task_name=(
                    "Scheduled RTP registry synchronization"
                    if schedule.target_type
                    is AutomationTargetType.RTP_REGISTRY_SYNC
                    else "Scheduled Pipeline handoff"
                ),
                status=notification_status,
                environment_url=self._environment_url,
                application_name=self._application_name,
                execution_engine="platform scheduler / REST",
                occurred_at=now,
                duration_seconds=0.0,
                job_or_integration_name=schedule.target_key,
                error_message=(
                    message
                    if notification_status is not TaskNotificationStatus.SUCCESS
                    else None
                ),
            )
        )

    def _adapter(
        self,
        target_type: AutomationTargetType,
    ) -> AutomationScheduleTargetAdapter:
        adapter = self._adapters.get(AutomationTargetType(target_type))
        if adapter is None:
            raise AutomationScheduleError(
                f"Scheduled target '{target_type.value}' is not enabled."
            )
        return adapter
