"""Application use cases for durable Planning Process schedules."""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.application.execution_manager import (
    PlanningProcessExecutionManager,
)
from app.application.planning_process import (
    PlanningProcessApplicationService,
    PlanningProcessInput,
)
from app.application.process_designer import (
    ProcessDesignerApplicationService,
)
from app.config.settings import Settings
from app.models.planning_process import ProcessContextMode
from app.models.process_schedule import (
    ProcessSchedule,
    ProcessScheduleInput,
    ScheduleContextMode,
    ScheduleFrequency,
    ScheduleRunOutcome,
)
from app.models.access_control import ExecutionActor, TriggerSource
from app.services.process_schedule_repository import (
    SQLProcessScheduleRepository,
)
from app.utils.exceptions import EPMError, PlanningProcessError, ScheduleError


@dataclass(frozen=True, slots=True)
class SchedulePresetOption:
    """Presentation-safe saved preset option."""

    preset_id: int
    name: str
    one_click_ready: bool
    year: str
    start_period: str
    end_period: str
    inbox_files: tuple[tuple[str, str], ...]
    required_upload_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScheduleProcessOption:
    """Approved Process and its unattended context choices."""

    code: str
    name: str
    context_mode: str
    supports_pipeline_defaults: bool
    presets: tuple[SchedulePresetOption, ...]


@dataclass(frozen=True, slots=True)
class ScheduleRunResult:
    """Result of handing one occurrence to the execution manager."""

    schedule_id: int
    outcome: ScheduleRunOutcome
    execution_id: str | None
    message: str


@dataclass(frozen=True, slots=True)
class SchedulePreview:
    """Validated next occurrence shown before a schedule is saved."""

    next_run_at: datetime
    next_run_local: datetime


class ProcessScheduleApplicationService:
    """Configure and execute approved Process recurrences."""

    def __init__(
        self,
        settings: Settings,
        *,
        process_service: PlanningProcessApplicationService | None = None,
        process_designer: ProcessDesignerApplicationService | None = None,
        execution_manager: PlanningProcessExecutionManager | None = None,
        repository: SQLProcessScheduleRepository | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._process_service = process_service or (
            PlanningProcessApplicationService(settings)
        )
        self._process_designer = process_designer or (
            ProcessDesignerApplicationService(settings)
        )
        self._execution_manager = execution_manager
        self._repository = repository or SQLProcessScheduleRepository(
            settings.database_target
        )
        self._logger = logger or logging.getLogger(__name__)

    def catalog(self) -> tuple[ScheduleProcessOption, ...]:
        """Return approved processes and reusable unattended contexts."""
        items = self._process_designer.list_workspace_processes()
        result: list[ScheduleProcessOption] = []
        for item in items:
            try:
                definition = self._process_service.get_definition(item.code)
            except EPMError:
                continue
            result.append(ScheduleProcessOption(
                code=item.code,
                name=item.display_name,
                context_mode=definition.context_mode.value,
                supports_pipeline_defaults=(
                    definition.context_mode
                    is ProcessContextMode.PIPELINE_DEFAULTS
                ),
                presets=tuple(
                    SchedulePresetOption(
                        preset_id=profile.profile_id,
                        name=profile.name,
                        one_click_ready=profile.one_click_ready,
                        year=profile.year,
                        start_period=profile.start_period,
                        end_period=profile.end_period,
                        inbox_files=profile.inbox_files,
                        required_upload_keys=profile.required_upload_keys,
                    )
                    for profile in self._process_designer.list_profiles(
                        item.code
                    )
                ),
            ))
        return tuple(result)

    def list_schedules(self) -> tuple[ProcessSchedule, ...]:
        """Return every active or paused schedule."""
        return self._repository.list_active()

    def create(self, schedule_input: ProcessScheduleInput) -> ProcessSchedule:
        """Validate live Process inputs and persist a recurrence."""
        normalized = self._normalize(schedule_input)
        self._validate_unattended_input(normalized)
        now = datetime.now(UTC)
        next_run = (
            self.next_occurrence(normalized, after=now)
            if normalized.enabled
            else None
        )
        if normalized.frequency is ScheduleFrequency.ONE_TIME and (
            next_run is None
        ):
            raise ScheduleError("A one-time schedule must be in the future.")
        return self._repository.create(
            normalized,
            next_run_at=next_run,
            now=now,
        )

    def preview(
        self,
        schedule_input: ProcessScheduleInput,
        *,
        now: datetime | None = None,
    ) -> SchedulePreview:
        """Validate unattended inputs and calculate the next occurrence."""
        normalized = self._normalize(schedule_input)
        self._validate_unattended_input(normalized)
        current = (now or datetime.now(UTC)).astimezone(UTC)
        next_run = self.next_occurrence(normalized, after=current)
        if next_run is None:
            raise ScheduleError("A one-time schedule must be in the future.")
        timezone = self._timezone(normalized.timezone)
        return SchedulePreview(
            next_run_at=next_run,
            next_run_local=next_run.astimezone(timezone),
        )

    def update(
        self,
        schedule_id: int,
        schedule_input: ProcessScheduleInput,
    ) -> ProcessSchedule:
        """Validate and replace one schedule definition."""
        if self._repository.get(schedule_id) is None:
            raise ScheduleError(f"Schedule {schedule_id} was not found.")
        normalized = self._normalize(schedule_input)
        self._validate_unattended_input(normalized)
        now = datetime.now(UTC)
        next_run = (
            self.next_occurrence(normalized, after=now)
            if normalized.enabled
            else None
        )
        if normalized.frequency is ScheduleFrequency.ONE_TIME and (
            normalized.enabled and next_run is None
        ):
            raise ScheduleError("A one-time schedule must be in the future.")
        return self._repository.update(
            schedule_id,
            normalized,
            next_run_at=next_run,
            now=now,
        )

    def set_enabled(
        self,
        schedule_id: int,
        enabled: bool,
    ) -> ProcessSchedule:
        """Pause or resume a schedule after revalidating its Process."""
        schedule = self._required(schedule_id)
        now = datetime.now(UTC)
        next_run = None
        if enabled:
            schedule_input = self._input_from_schedule(schedule, enabled=True)
            self._validate_unattended_input(schedule_input)
            next_run = self.next_occurrence(schedule_input, after=now)
            if next_run is None:
                raise ScheduleError(
                    "This one-time schedule has expired. Edit its first run "
                    "before resuming it."
                )
        return self._repository.set_enabled(
            schedule_id,
            enabled=enabled,
            next_run_at=next_run,
            now=now,
        )

    def archive(self, schedule_id: int) -> None:
        """Archive one schedule without deleting Process execution history."""
        self._repository.archive(schedule_id, now=datetime.now(UTC))

    def run_now(
        self,
        schedule_id: int,
        *,
        actor: ExecutionActor | None = None,
    ) -> ScheduleRunResult:
        """Validate and submit the configured Process immediately."""
        return self._submit(
            self._required(schedule_id),
            triggered_at=datetime.now(UTC),
            actor=actor,
        )

    def run_due(self, *, now: datetime | None = None) -> tuple[ScheduleRunResult, ...]:
        """Atomically claim and submit all due Process occurrences."""
        current = (now or datetime.now(UTC)).astimezone(UTC)
        results: list[ScheduleRunResult] = []
        for schedule in self._repository.list_due(current):
            schedule_input = self._input_from_schedule(schedule, enabled=True)
            next_run = self.next_occurrence(schedule_input, after=current)
            if not self._repository.claim(
                schedule,
                next_run_at=next_run,
                triggered_at=current,
            ):
                continue
            results.append(
                self._submit(
                    schedule,
                    triggered_at=current,
                    actor=ExecutionActor(
                        username="scheduler",
                        display_name="Process Scheduler",
                        trigger_source=TriggerSource.SCHEDULED,
                    ),
                )
            )
        return tuple(results)

    def next_occurrence(
        self,
        schedule_input: ProcessScheduleInput,
        *,
        after: datetime,
    ) -> datetime | None:
        """Return the first occurrence strictly after an instant."""
        if after.tzinfo is None:
            raise ScheduleError("The recurrence reference time requires a timezone.")
        timezone = self._timezone(schedule_input.timezone)
        anchor_local = schedule_input.first_run_local.replace(tzinfo=timezone)
        after_utc = after.astimezone(UTC)
        if anchor_local.astimezone(UTC) > after_utc:
            return anchor_local.astimezone(UTC)
        if schedule_input.frequency is ScheduleFrequency.ONE_TIME:
            return None

        local_after = after_utc.astimezone(timezone)
        anchor_date = anchor_local.date()
        anchor_time = time(
            anchor_local.hour,
            anchor_local.minute,
            anchor_local.second,
            anchor_local.microsecond,
        )
        if schedule_input.frequency is ScheduleFrequency.DAILY:
            days = max(0, (local_after.date() - anchor_date).days)
            candidate_date = anchor_date + timedelta(days=days)
            candidate = datetime.combine(candidate_date, anchor_time, timezone)
            if candidate.astimezone(UTC) <= after_utc:
                candidate = datetime.combine(
                    candidate_date + timedelta(days=1),
                    anchor_time,
                    timezone,
                )
            return candidate.astimezone(UTC)

        if schedule_input.frequency is ScheduleFrequency.WEEKLY:
            days = max(0, (local_after.date() - anchor_date).days)
            weeks = days // 7
            candidate_date = anchor_date + timedelta(weeks=weeks)
            candidate = datetime.combine(candidate_date, anchor_time, timezone)
            if candidate.astimezone(UTC) <= after_utc:
                candidate = datetime.combine(
                    candidate_date + timedelta(weeks=1),
                    anchor_time,
                    timezone,
                )
            return candidate.astimezone(UTC)

        months = max(
            0,
            (local_after.year - anchor_date.year) * 12
            + local_after.month
            - anchor_date.month,
        )
        candidate = self._monthly_candidate(
            anchor_date,
            anchor_time,
            timezone,
            months,
        )
        if candidate.astimezone(UTC) <= after_utc:
            candidate = self._monthly_candidate(
                anchor_date,
                anchor_time,
                timezone,
                months + 1,
            )
        return candidate.astimezone(UTC)

    def _submit(
        self,
        schedule: ProcessSchedule,
        *,
        triggered_at: datetime,
        actor: ExecutionActor | None = None,
    ) -> ScheduleRunResult:
        if self._execution_manager is None:
            raise ScheduleError("The Process execution manager is unavailable.")
        try:
            process_input = self._validated_process_input(schedule)
            execution = self._execution_manager.submit(
                process_input,
                actor=actor,
            )
        except PlanningProcessError as exc:
            outcome = (
                ScheduleRunOutcome.SKIPPED
                if "active execution" in str(exc)
                else ScheduleRunOutcome.FAILED
            )
            self._record_failure(schedule, outcome, exc, triggered_at)
            return ScheduleRunResult(
                schedule_id=schedule.schedule_id,
                outcome=outcome,
                execution_id=None,
                message=str(exc),
            )
        except EPMError as exc:
            self._record_failure(
                schedule,
                ScheduleRunOutcome.FAILED,
                exc,
                triggered_at,
            )
            return ScheduleRunResult(
                schedule_id=schedule.schedule_id,
                outcome=ScheduleRunOutcome.FAILED,
                execution_id=None,
                message=str(exc),
            )
        except Exception as exc:
            self._logger.exception(
                "Unexpected scheduled Process submission failure: "
                "schedule_id=%s.",
                schedule.schedule_id,
            )
            self._record_failure(
                schedule,
                ScheduleRunOutcome.FAILED,
                exc,
                triggered_at,
            )
            return ScheduleRunResult(
                schedule_id=schedule.schedule_id,
                outcome=ScheduleRunOutcome.FAILED,
                execution_id=None,
                message="Unexpected scheduling failure. Review the server log.",
            )
        self._repository.record_result(
            schedule.schedule_id,
            outcome=ScheduleRunOutcome.SUBMITTED,
            execution_id=execution.execution_id,
            error=None,
            now=datetime.now(UTC),
            triggered_at=triggered_at,
        )
        self._logger.info(
            "Scheduled Planning Process submitted: schedule_id=%s, "
            "process='%s', execution_id='%s'.",
            schedule.schedule_id,
            schedule.process_code,
            execution.execution_id,
        )
        return ScheduleRunResult(
            schedule_id=schedule.schedule_id,
            outcome=ScheduleRunOutcome.SUBMITTED,
            execution_id=execution.execution_id,
            message="Planning Process was submitted successfully.",
        )

    def _validate_unattended_input(
        self,
        schedule_input: ProcessScheduleInput,
    ) -> None:
        probe = replace(
            schedule_input,
            enabled=True,
        )
        self._validated_process_input(probe)

    def _validated_process_input(
        self,
        schedule: ProcessSchedule | ProcessScheduleInput,
    ) -> PlanningProcessInput:
        definition = self._process_service.get_definition(
            schedule.process_code
        )
        if schedule.context_mode is ScheduleContextMode.RUN_PRESET:
            if schedule.preset_id is None:
                raise ScheduleError("Select a saved run preset.")
            profile = self._process_designer.get_profile(
                definition.code,
                schedule.preset_id,
            )
            if profile.required_upload_keys:
                raise ScheduleError(
                    f"Run preset '{profile.name}' requires a local upload "
                    "and cannot run unattended. Use an Oracle Inbox file."
                )
            self._process_designer.validate_profile(
                definition.code,
                profile.profile_id,
            )
            process_input = self._process_designer.profile_process_input(
                definition.code,
                profile.profile_id,
            )
        else:
            if definition.context_mode is not ProcessContextMode.PIPELINE_DEFAULTS:
                raise ScheduleError(
                    f"Process '{definition.display_name}' prompts for runtime "
                    "context. Select a saved run preset before scheduling it."
                )
            process_input = PlanningProcessInput(
                process_code=definition.code,
                year="",
                start_period="",
                end_period="",
            )
        preflight = self._process_service.preflight(process_input)
        supplied_inbox = {
            key.casefold() for key in process_input.pipeline_inbox_files
        }
        missing_files = [
            item.display_name
            for item in preflight.file_requirements
            if item.required
            and not item.configured_reference
            and item.key.casefold() not in supplied_inbox
        ]
        if missing_files:
            raise ScheduleError(
                "Unattended execution requires configured Oracle Inbox "
                "files for: " + ", ".join(missing_files)
            )
        return process_input

    def _record_failure(
        self,
        schedule: ProcessSchedule,
        outcome: ScheduleRunOutcome,
        error: Exception,
        triggered_at: datetime,
    ) -> None:
        message = " ".join(str(error).split())[:1_500]
        self._repository.record_result(
            schedule.schedule_id,
            outcome=outcome,
            execution_id=None,
            error=message,
            now=datetime.now(UTC),
            triggered_at=triggered_at,
        )
        self._logger.warning(
            "Scheduled Planning Process was not submitted: schedule_id=%s, "
            "outcome=%s, error=%s.",
            schedule.schedule_id,
            outcome.value,
            message,
        )

    def _normalize(
        self,
        schedule_input: ProcessScheduleInput,
    ) -> ProcessScheduleInput:
        name = str(schedule_input.name).strip()
        process_code = str(schedule_input.process_code).strip().upper()
        timezone = str(schedule_input.timezone).strip()
        if not name or not process_code or not timezone:
            raise ScheduleError(
                "Schedule name, Process, and timezone are required."
            )
        if schedule_input.first_run_local.tzinfo is not None:
            raise ScheduleError(
                "First run must be a local date and time without an offset."
            )
        self._timezone(timezone)
        preset_id = schedule_input.preset_id
        if schedule_input.context_mode is ScheduleContextMode.RUN_PRESET:
            if preset_id is None or preset_id <= 0:
                raise ScheduleError("Select a saved run preset.")
        else:
            preset_id = None
        return ProcessScheduleInput(
            name=name,
            process_code=process_code,
            frequency=ScheduleFrequency(schedule_input.frequency),
            timezone=timezone,
            first_run_local=schedule_input.first_run_local,
            context_mode=ScheduleContextMode(schedule_input.context_mode),
            preset_id=preset_id,
            enabled=bool(schedule_input.enabled),
        )

    def _required(self, schedule_id: int) -> ProcessSchedule:
        schedule = self._repository.get(schedule_id)
        if schedule is None:
            raise ScheduleError(f"Schedule {schedule_id} was not found.")
        return schedule

    @staticmethod
    def _input_from_schedule(
        schedule: ProcessSchedule,
        *,
        enabled: bool,
    ) -> ProcessScheduleInput:
        return ProcessScheduleInput(
            name=schedule.name,
            process_code=schedule.process_code,
            frequency=schedule.frequency,
            timezone=schedule.timezone,
            first_run_local=schedule.first_run_local,
            context_mode=schedule.context_mode,
            preset_id=schedule.preset_id,
            enabled=enabled,
        )

    @staticmethod
    def _timezone(name: str) -> ZoneInfo:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ScheduleError(f"Timezone '{name}' is not available.") from exc

    @staticmethod
    def _monthly_candidate(
        anchor: date,
        anchor_time: time,
        timezone: ZoneInfo,
        offset: int,
    ) -> datetime:
        total_month = anchor.year * 12 + anchor.month - 1 + offset
        year, month_index = divmod(total_month, 12)
        month = month_index + 1
        day = min(anchor.day, calendar.monthrange(year, month)[1])
        return datetime.combine(date(year, month, day), anchor_time, timezone)
