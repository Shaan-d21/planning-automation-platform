"""Application use cases for generic durable automation schedules."""

from __future__ import annotations

import calendar
import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.infrastructure.database.engine import DatabaseTarget
from app.models.automation_schedule import (
    AutomationConcurrencyPolicy,
    AutomationInputPolicy,
    AutomationMisfirePolicy,
    AutomationSchedule,
    AutomationScheduleFrequency,
    AutomationScheduleInput,
    AutomationSchedulePreview,
    AutomationScheduleRun,
    AutomationScheduleRunEvidence,
    AutomationScheduleRunStatus,
    AutomationTargetType,
)
from app.services.automation_schedule_repository import (
    SQLAutomationScheduleRepository,
)
from app.utils.exceptions import AutomationScheduleError


class AutomationScheduleApplicationService:
    """Manage recurrences without coupling them to an Oracle transport."""

    _SENSITIVE_CONFIGURATION_KEYS = {
        "apikey",
        "credential",
        "password",
        "privatekey",
        "secret",
        "token",
    }

    def __init__(
        self,
        database_target: DatabaseTarget,
        *,
        repository: SQLAutomationScheduleRepository | None = None,
    ) -> None:
        self._repository = repository or SQLAutomationScheduleRepository(
            database_target
        )

    def preview(
        self,
        schedule_input: AutomationScheduleInput,
        *,
        now: datetime | None = None,
    ) -> AutomationSchedulePreview:
        """Validate a definition and calculate its next occurrence."""
        normalized = self._normalize(schedule_input)
        current = self._utc(now)
        next_run = self.next_occurrence(normalized, after=current)
        if next_run is None:
            raise AutomationScheduleError(
                "A one-time schedule must have a future execution time."
            )
        timezone = self._timezone(normalized.timezone)
        return AutomationSchedulePreview(
            next_run_at=next_run,
            next_run_local=next_run.astimezone(timezone),
        )

    def create(
        self,
        schedule_input: AutomationScheduleInput,
        *,
        now: datetime | None = None,
    ) -> AutomationSchedule:
        """Validate and persist a platform-owned recurrence."""
        current = self._utc(now)
        normalized = self._normalize(schedule_input)
        candidate = self.next_occurrence(normalized, after=current)
        if (
            normalized.frequency is AutomationScheduleFrequency.ONE_TIME
            and candidate is None
        ):
            raise AutomationScheduleError(
                "A one-time schedule must have a future execution time."
            )
        return self._repository.create(
            normalized,
            next_run_at=candidate if normalized.enabled else None,
            now=current,
        )

    def update(
        self,
        schedule_id: int,
        schedule_input: AutomationScheduleInput,
        *,
        now: datetime | None = None,
    ) -> AutomationSchedule:
        """Validate and replace one schedule definition."""
        self._repository.require(schedule_id)
        current = self._utc(now)
        normalized = self._normalize(schedule_input)
        candidate = self.next_occurrence(normalized, after=current)
        if (
            normalized.frequency is AutomationScheduleFrequency.ONE_TIME
            and candidate is None
        ):
            raise AutomationScheduleError(
                "A one-time schedule must have a future execution time."
            )
        return self._repository.update(
            schedule_id,
            normalized,
            next_run_at=candidate if normalized.enabled else None,
            now=current,
        )

    def get(self, schedule_id: int) -> AutomationSchedule:
        """Return one active or paused schedule."""
        return self._repository.require(schedule_id)

    def list_schedules(
        self,
        *,
        environment_key: str | None = None,
    ) -> tuple[AutomationSchedule, ...]:
        """Return schedules, optionally restricted to one environment."""
        normalized_environment = (
            self._required_text(environment_key, "Environment key", 64)
            if environment_key is not None
            else None
        )
        return self._repository.list_active(
            environment_key=normalized_environment
        )

    def set_enabled(
        self,
        schedule_id: int,
        enabled: bool,
        *,
        now: datetime | None = None,
    ) -> AutomationSchedule:
        """Pause or resume one recurrence."""
        schedule = self._repository.require(schedule_id)
        current = self._utc(now)
        next_run = None
        if enabled:
            schedule_input = self._input_from_schedule(schedule, enabled=True)
            next_run = self.next_occurrence(schedule_input, after=current)
            if next_run is None:
                raise AutomationScheduleError(
                    "This one-time schedule has expired. Edit its execution "
                    "time before resuming it."
                )
        return self._repository.set_enabled(
            schedule_id,
            enabled=bool(enabled),
            next_run_at=next_run,
            now=current,
        )

    def archive(
        self,
        schedule_id: int,
        *,
        now: datetime | None = None,
    ) -> None:
        """Archive a schedule while retaining its run history."""
        self._repository.archive(schedule_id, now=self._utc(now))

    def claim_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> tuple[AutomationScheduleRun, ...]:
        """Claim due occurrences for a later execution-dispatch step."""
        current = self._utc(now)
        claimed: list[AutomationScheduleRun] = []
        for schedule in self._repository.list_due(current, limit=limit):
            schedule_input = self._input_from_schedule(schedule, enabled=True)
            next_run = self.next_occurrence(schedule_input, after=current)
            run = self._repository.claim(
                schedule,
                next_run_at=next_run,
                claimed_at=current,
                resolved_payload=self._resolved_payload(schedule),
            )
            if run is None:
                continue
            if schedule.misfire_policy is AutomationMisfirePolicy.SKIP and (
                schedule.next_run_at is not None
                and schedule.next_run_at < current
            ):
                self._repository.record_run_result(
                    run.run_id,
                    status=AutomationScheduleRunStatus.SKIPPED,
                    execution_id=None,
                    error_message="Occurrence was skipped by its misfire policy.",
                    now=current,
                )
                continue
            claimed.append(run)
        return tuple(claimed)

    def record_submitted(
        self,
        run_id: int,
        execution_id: str,
        *,
        now: datetime | None = None,
    ) -> AutomationScheduleRun:
        """Link a claimed occurrence to durable execution work."""
        normalized_execution_id = self._required_text(
            execution_id,
            "Execution ID",
            64,
        )
        return self._repository.record_run_result(
            run_id,
            status=AutomationScheduleRunStatus.SUBMITTED,
            execution_id=normalized_execution_id,
            error_message=None,
            now=self._utc(now),
        )

    def record_completed(
        self,
        run_id: int,
        *,
        now: datetime | None = None,
    ) -> AutomationScheduleRun:
        """Persist completion for synchronous scheduled maintenance work."""
        return self._repository.record_run_result(
            run_id,
            status=AutomationScheduleRunStatus.COMPLETED,
            execution_id=None,
            error_message=None,
            now=self._utc(now),
        )

    def record_failed(
        self,
        run_id: int,
        error: str,
        *,
        now: datetime | None = None,
    ) -> AutomationScheduleRun:
        """Persist a safe scheduler-handoff failure."""
        message = " ".join(str(error).split())[:1_500]
        if not message:
            message = "Scheduled automation could not be submitted."
        return self._repository.record_run_result(
            run_id,
            status=AutomationScheduleRunStatus.FAILED,
            execution_id=None,
            error_message=message,
            now=self._utc(now),
        )

    def record_skipped(
        self,
        run_id: int,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> AutomationScheduleRun:
        """Persist a governed skip without treating it as a system failure."""
        message = " ".join(str(reason).split())[:1_500]
        if not message:
            message = "Scheduled automation was skipped."
        return self._repository.record_run_result(
            run_id,
            status=AutomationScheduleRunStatus.SKIPPED,
            execution_id=None,
            error_message=message,
            now=self._utc(now),
        )

    def list_runs(
        self,
        schedule_id: int,
        *,
        limit: int = 100,
    ) -> tuple[AutomationScheduleRun, ...]:
        """Return recent occurrence evidence for one schedule."""
        self._repository.require(schedule_id)
        return self._repository.list_runs(schedule_id, limit=limit)

    def list_run_evidence(
        self,
        environment_key: str,
        *,
        schedule_id: int | None = None,
        status: AutomationScheduleRunStatus | None = None,
        scheduled_from: datetime | None = None,
        scheduled_to: datetime | None = None,
        limit: int = 100,
    ) -> tuple[AutomationScheduleRunEvidence, ...]:
        """Return filtered occurrence evidence for one Oracle environment."""
        normalized_environment = self._required_text(
            environment_key,
            "Environment key",
            64,
        )
        if schedule_id is not None and schedule_id <= 0:
            raise AutomationScheduleError("Schedule ID must be positive.")
        if scheduled_from is not None:
            scheduled_from = self._utc(scheduled_from)
        if scheduled_to is not None:
            scheduled_to = self._utc(scheduled_to)
        if (
            scheduled_from is not None
            and scheduled_to is not None
            and scheduled_from > scheduled_to
        ):
            raise AutomationScheduleError(
                "Schedule history start must not be after its end."
            )
        normalized_status = (
            AutomationScheduleRunStatus(status) if status is not None else None
        )
        return self._repository.list_run_evidence(
            normalized_environment,
            schedule_id=schedule_id,
            status=normalized_status,
            scheduled_from=scheduled_from,
            scheduled_to=scheduled_to,
            limit=max(1, min(int(limit), 1_000)),
        )

    def next_occurrence(
        self,
        schedule_input: AutomationScheduleInput,
        *,
        after: datetime,
    ) -> datetime | None:
        """Return the first occurrence strictly after an aware instant."""
        if after.tzinfo is None:
            raise AutomationScheduleError(
                "The recurrence reference time requires a timezone."
            )
        timezone = self._timezone(schedule_input.timezone)
        anchor_local = schedule_input.first_run_local.replace(tzinfo=timezone)
        after_utc = after.astimezone(UTC)
        if anchor_local.astimezone(UTC) > after_utc:
            return anchor_local.astimezone(UTC)
        if schedule_input.frequency is AutomationScheduleFrequency.ONE_TIME:
            return None

        local_after = after_utc.astimezone(timezone)
        anchor_date = anchor_local.date()
        anchor_time = time(
            anchor_local.hour,
            anchor_local.minute,
            anchor_local.second,
            anchor_local.microsecond,
        )
        if schedule_input.frequency is AutomationScheduleFrequency.DAILY:
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

        if schedule_input.frequency is AutomationScheduleFrequency.WEEKLY:
            days = max(0, (local_after.date() - anchor_date).days)
            candidate_date = anchor_date + timedelta(weeks=days // 7)
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

    def _normalize(
        self,
        schedule_input: AutomationScheduleInput,
    ) -> AutomationScheduleInput:
        configuration = self._configuration(schedule_input.configuration)
        first_run = schedule_input.first_run_local
        if first_run.tzinfo is not None:
            raise AutomationScheduleError(
                "First run must be a local date and time without an offset."
            )
        timezone = self._required_text(
            schedule_input.timezone,
            "Timezone",
            64,
        )
        self._timezone(timezone)
        return AutomationScheduleInput(
            environment_key=self._required_text(
                schedule_input.environment_key,
                "Environment key",
                64,
            ),
            name=self._required_text(schedule_input.name, "Schedule name", 160),
            target_type=AutomationTargetType(schedule_input.target_type),
            target_key=self._required_text(
                schedule_input.target_key,
                "Target",
                300,
            ),
            frequency=AutomationScheduleFrequency(schedule_input.frequency),
            timezone=timezone,
            first_run_local=first_run,
            input_policy=AutomationInputPolicy(schedule_input.input_policy),
            configuration=configuration,
            concurrency_policy=AutomationConcurrencyPolicy(
                schedule_input.concurrency_policy
            ),
            misfire_policy=AutomationMisfirePolicy(
                schedule_input.misfire_policy
            ),
            enabled=bool(schedule_input.enabled),
        )

    def _configuration(self, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise AutomationScheduleError(
                "Schedule configuration must be a JSON object."
            )
        self._reject_sensitive_values(value)
        try:
            return json.loads(json.dumps(value))
        except (TypeError, ValueError) as exc:
            raise AutomationScheduleError(
                "Schedule configuration must contain only JSON values."
            ) from exc

    def _reject_sensitive_values(self, value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise AutomationScheduleError(
                        "Schedule configuration keys must be text."
                    )
                normalized = "".join(
                    character for character in key.casefold() if character.isalnum()
                )
                if any(
                    sensitive in normalized
                    for sensitive in self._SENSITIVE_CONFIGURATION_KEYS
                ):
                    raise AutomationScheduleError(
                        "Credentials and secrets cannot be stored in a schedule."
                    )
                self._reject_sensitive_values(child)
        elif isinstance(value, list):
            for child in value:
                self._reject_sensitive_values(child)

    @staticmethod
    def _resolved_payload(schedule: AutomationSchedule) -> dict[str, Any]:
        return {
            "environment_key": schedule.environment_key,
            "target_type": schedule.target_type.value,
            "target_key": schedule.target_key,
            "input_policy": schedule.input_policy.value,
            "configuration": json.loads(json.dumps(schedule.configuration)),
        }

    @staticmethod
    def _input_from_schedule(
        schedule: AutomationSchedule,
        *,
        enabled: bool,
    ) -> AutomationScheduleInput:
        return AutomationScheduleInput(
            environment_key=schedule.environment_key,
            name=schedule.name,
            target_type=schedule.target_type,
            target_key=schedule.target_key,
            frequency=schedule.frequency,
            timezone=schedule.timezone,
            first_run_local=schedule.first_run_local,
            input_policy=schedule.input_policy,
            configuration=schedule.configuration,
            concurrency_policy=schedule.concurrency_policy,
            misfire_policy=schedule.misfire_policy,
            enabled=enabled,
        )

    @staticmethod
    def _required_text(value: object, label: str, maximum: int) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise AutomationScheduleError(f"{label} is required.")
        if len(normalized) > maximum:
            raise AutomationScheduleError(
                f"{label} cannot exceed {maximum} characters."
            )
        return normalized

    @staticmethod
    def _utc(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise AutomationScheduleError(
                "Scheduler timestamps must include a timezone."
            )
        return current.astimezone(UTC)

    @staticmethod
    def _timezone(name: str) -> ZoneInfo:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise AutomationScheduleError(
                f"Timezone '{name}' is not available."
            ) from exc

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
        return datetime.combine(
            date(year, month, day),
            anchor_time,
            timezone,
        )
