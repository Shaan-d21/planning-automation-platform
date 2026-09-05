"""PostgreSQL persistence for generic platform automation schedules."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import case, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database.engine import (
    DatabaseTarget,
    database_for,
    utc_datetime,
)
from app.infrastructure.database.schema import (
    automation_schedule_runs,
    automation_schedules,
)
from app.models.automation_schedule import (
    AutomationConcurrencyPolicy,
    AutomationInputPolicy,
    AutomationMisfirePolicy,
    AutomationSchedule,
    AutomationScheduleFrequency,
    AutomationScheduleInput,
    AutomationScheduleOutcome,
    AutomationScheduleRun,
    AutomationScheduleRunEvidence,
    AutomationScheduleRunStatus,
    AutomationTargetType,
)
from app.utils.exceptions import AutomationScheduleError


class SQLAutomationScheduleRepository:
    """Persist recurrences and atomically claim due occurrences."""

    def __init__(self, database_target: DatabaseTarget) -> None:
        self._database = database_for(database_target)

    def create(
        self,
        schedule_input: AutomationScheduleInput,
        *,
        next_run_at: datetime | None,
        now: datetime,
    ) -> AutomationSchedule:
        try:
            with self._database.begin() as connection:
                schedule_id = connection.execute(
                    insert(automation_schedules)
                    .values(
                        **self._input_values(schedule_input),
                        next_run_at=next_run_at,
                        created_at=now,
                        updated_at=now,
                        last_outcome=AutomationScheduleOutcome.NEVER.value,
                    )
                    .returning(automation_schedules.c.schedule_id)
                ).scalar_one()
        except IntegrityError as exc:
            raise AutomationScheduleError(
                f"An active schedule named '{schedule_input.name}' already "
                "exists for this Oracle environment."
            ) from exc
        return self.require(int(schedule_id))

    def update(
        self,
        schedule_id: int,
        schedule_input: AutomationScheduleInput,
        *,
        next_run_at: datetime | None,
        now: datetime,
    ) -> AutomationSchedule:
        try:
            with self._database.begin() as connection:
                result = connection.execute(
                    update(automation_schedules)
                    .where(
                        automation_schedules.c.schedule_id == schedule_id,
                        automation_schedules.c.archived_at.is_(None),
                    )
                    .values(
                        **self._input_values(schedule_input),
                        next_run_at=next_run_at,
                        updated_at=now,
                        last_error=None,
                    )
                )
        except IntegrityError as exc:
            raise AutomationScheduleError(
                f"An active schedule named '{schedule_input.name}' already "
                "exists for this Oracle environment."
            ) from exc
        if result.rowcount != 1:
            raise AutomationScheduleError(
                f"Automation schedule {schedule_id} was not found."
            )
        return self.require(schedule_id)

    def get(self, schedule_id: int) -> AutomationSchedule | None:
        with self._database.connect() as connection:
            row = connection.execute(
                select(automation_schedules).where(
                    automation_schedules.c.schedule_id == schedule_id,
                    automation_schedules.c.archived_at.is_(None),
                )
            ).mappings().one_or_none()
        return self._schedule(row) if row is not None else None

    def require(self, schedule_id: int) -> AutomationSchedule:
        schedule = self.get(schedule_id)
        if schedule is None:
            raise AutomationScheduleError(
                f"Automation schedule {schedule_id} was not found."
            )
        return schedule

    def list_active(
        self,
        *,
        environment_key: str | None = None,
    ) -> tuple[AutomationSchedule, ...]:
        statement = select(automation_schedules).where(
            automation_schedules.c.archived_at.is_(None)
        )
        if environment_key is not None:
            statement = statement.where(
                automation_schedules.c.environment_key == environment_key
            )
        statement = statement.order_by(
            automation_schedules.c.is_enabled.desc(),
            case(
                (automation_schedules.c.next_run_at.is_(None), 1),
                else_=0,
            ),
            automation_schedules.c.next_run_at,
            func.lower(automation_schedules.c.name),
        )
        with self._database.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(self._schedule(row) for row in rows)

    def list_due(
        self,
        now: datetime,
        *,
        limit: int = 100,
    ) -> tuple[AutomationSchedule, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                select(automation_schedules)
                .where(
                    automation_schedules.c.archived_at.is_(None),
                    automation_schedules.c.is_enabled.is_(True),
                    automation_schedules.c.next_run_at.is_not(None),
                    automation_schedules.c.next_run_at <= now,
                )
                .order_by(
                    automation_schedules.c.next_run_at,
                    automation_schedules.c.schedule_id,
                )
                .limit(max(1, min(int(limit), 1_000)))
            ).mappings().all()
        return tuple(self._schedule(row) for row in rows)

    def claim(
        self,
        schedule: AutomationSchedule,
        *,
        next_run_at: datetime | None,
        claimed_at: datetime,
        resolved_payload: dict[str, Any],
    ) -> AutomationScheduleRun | None:
        """Atomically advance a recurrence and create its immutable occurrence."""
        scheduled_for = schedule.next_run_at
        if scheduled_for is None:
            return None
        try:
            with self._database.begin() as connection:
                result = connection.execute(
                    update(automation_schedules)
                    .where(
                        automation_schedules.c.schedule_id
                        == schedule.schedule_id,
                        automation_schedules.c.archived_at.is_(None),
                        automation_schedules.c.is_enabled.is_(True),
                        automation_schedules.c.next_run_at == scheduled_for,
                    )
                    .values(
                        next_run_at=next_run_at,
                        is_enabled=next_run_at is not None,
                        last_triggered_at=claimed_at,
                        last_outcome=AutomationScheduleOutcome.CLAIMED.value,
                        last_error=None,
                        updated_at=claimed_at,
                    )
                )
                if result.rowcount != 1:
                    return None
                run_id = connection.execute(
                    insert(automation_schedule_runs)
                    .values(
                        schedule_id=schedule.schedule_id,
                        scheduled_for=scheduled_for,
                        claimed_at=claimed_at,
                        status=AutomationScheduleRunStatus.CLAIMED.value,
                        resolved_payload=resolved_payload,
                    )
                    .returning(automation_schedule_runs.c.run_id)
                ).scalar_one()
        except IntegrityError:
            return None
        return self.require_run(int(run_id))

    def set_enabled(
        self,
        schedule_id: int,
        *,
        enabled: bool,
        next_run_at: datetime | None,
        now: datetime,
    ) -> AutomationSchedule:
        with self._database.begin() as connection:
            result = connection.execute(
                update(automation_schedules)
                .where(
                    automation_schedules.c.schedule_id == schedule_id,
                    automation_schedules.c.archived_at.is_(None),
                )
                .values(
                    is_enabled=enabled,
                    next_run_at=next_run_at,
                    updated_at=now,
                    last_error=None,
                )
            )
        if result.rowcount != 1:
            raise AutomationScheduleError(
                f"Automation schedule {schedule_id} was not found."
            )
        return self.require(schedule_id)

    def archive(self, schedule_id: int, *, now: datetime) -> None:
        with self._database.begin() as connection:
            result = connection.execute(
                update(automation_schedules)
                .where(
                    automation_schedules.c.schedule_id == schedule_id,
                    automation_schedules.c.archived_at.is_(None),
                )
                .values(
                    archived_at=now,
                    is_enabled=False,
                    next_run_at=None,
                    updated_at=now,
                )
            )
        if result.rowcount != 1:
            raise AutomationScheduleError(
                f"Automation schedule {schedule_id} was not found."
            )

    def record_run_result(
        self,
        run_id: int,
        *,
        status: AutomationScheduleRunStatus,
        execution_id: str | None,
        error_message: str | None,
        now: datetime,
    ) -> AutomationScheduleRun:
        outcome = AutomationScheduleOutcome(status.value)
        with self._database.begin() as connection:
            run = connection.execute(
                select(automation_schedule_runs).where(
                    automation_schedule_runs.c.run_id == run_id
                )
            ).mappings().one_or_none()
            if run is None:
                raise AutomationScheduleError(
                    f"Automation schedule run {run_id} was not found."
                )
            result = connection.execute(
                update(automation_schedule_runs)
                .where(
                    automation_schedule_runs.c.run_id == run_id,
                    automation_schedule_runs.c.status
                    == AutomationScheduleRunStatus.CLAIMED.value,
                )
                .values(
                    status=status.value,
                    completed_at=now,
                    execution_id=execution_id,
                    error_message=error_message,
                )
            )
            if result.rowcount != 1:
                raise AutomationScheduleError(
                    f"Automation schedule run {run_id} is already complete."
                )
            connection.execute(
                update(automation_schedules)
                .where(
                    automation_schedules.c.schedule_id == run["schedule_id"]
                )
                .values(
                    last_execution_id=execution_id,
                    last_outcome=outcome.value,
                    last_error=error_message,
                    updated_at=now,
                )
            )
        return self.require_run(run_id)

    def require_run(self, run_id: int) -> AutomationScheduleRun:
        with self._database.connect() as connection:
            row = connection.execute(
                select(automation_schedule_runs).where(
                    automation_schedule_runs.c.run_id == run_id
                )
            ).mappings().one_or_none()
        if row is None:
            raise AutomationScheduleError(
                f"Automation schedule run {run_id} was not found."
            )
        return self._run(row)

    def list_runs(
        self,
        schedule_id: int,
        *,
        limit: int = 100,
    ) -> tuple[AutomationScheduleRun, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                select(automation_schedule_runs)
                .where(automation_schedule_runs.c.schedule_id == schedule_id)
                .order_by(
                    automation_schedule_runs.c.scheduled_for.desc(),
                    automation_schedule_runs.c.run_id.desc(),
                )
                .limit(max(1, min(int(limit), 1_000)))
            ).mappings().all()
        return tuple(self._run(row) for row in rows)

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
        """Return environment-scoped occurrence evidence for monitoring."""
        statement = (
            select(
                automation_schedule_runs,
                automation_schedules.c.name.label("schedule_name"),
                automation_schedules.c.target_type.label("schedule_target_type"),
                automation_schedules.c.target_key.label("schedule_target_key"),
            )
            .join(
                automation_schedules,
                automation_schedules.c.schedule_id
                == automation_schedule_runs.c.schedule_id,
            )
            .where(automation_schedules.c.environment_key == environment_key)
        )
        if schedule_id is not None:
            statement = statement.where(
                automation_schedule_runs.c.schedule_id == schedule_id
            )
        if status is not None:
            statement = statement.where(
                automation_schedule_runs.c.status == status.value
            )
        if scheduled_from is not None:
            statement = statement.where(
                automation_schedule_runs.c.scheduled_for >= scheduled_from
            )
        if scheduled_to is not None:
            statement = statement.where(
                automation_schedule_runs.c.scheduled_for <= scheduled_to
            )
        statement = statement.order_by(
            automation_schedule_runs.c.scheduled_for.desc(),
            automation_schedule_runs.c.run_id.desc(),
        ).limit(max(1, min(int(limit), 1_000)))
        with self._database.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(
            AutomationScheduleRunEvidence(
                run=self._run(row),
                schedule_name=str(row["schedule_name"]),
                target_type=AutomationTargetType(
                    str(row["schedule_target_type"])
                ),
                target_key=str(row["schedule_target_key"]),
            )
            for row in rows
        )

    @staticmethod
    def _input_values(
        schedule_input: AutomationScheduleInput,
    ) -> dict[str, Any]:
        return {
            "environment_key": schedule_input.environment_key,
            "name": schedule_input.name,
            "target_type": schedule_input.target_type.value,
            "target_key": schedule_input.target_key,
            "frequency": schedule_input.frequency.value,
            "timezone": schedule_input.timezone,
            "first_run_local": schedule_input.first_run_local,
            "input_policy": schedule_input.input_policy.value,
            "configuration": schedule_input.configuration,
            "concurrency_policy": schedule_input.concurrency_policy.value,
            "misfire_policy": schedule_input.misfire_policy.value,
            "is_enabled": schedule_input.enabled,
        }

    @staticmethod
    def _schedule(row) -> AutomationSchedule:
        return AutomationSchedule(
            schedule_id=int(row["schedule_id"]),
            environment_key=str(row["environment_key"]),
            name=str(row["name"]),
            target_type=AutomationTargetType(str(row["target_type"])),
            target_key=str(row["target_key"]),
            frequency=AutomationScheduleFrequency(str(row["frequency"])),
            timezone=str(row["timezone"]),
            first_run_local=row["first_run_local"],
            input_policy=AutomationInputPolicy(str(row["input_policy"])),
            configuration=dict(row["configuration"] or {}),
            concurrency_policy=AutomationConcurrencyPolicy(
                str(row["concurrency_policy"])
            ),
            misfire_policy=AutomationMisfirePolicy(str(row["misfire_policy"])),
            enabled=bool(row["is_enabled"]),
            next_run_at=utc_datetime(row["next_run_at"]),
            created_at=utc_datetime(row["created_at"]),
            updated_at=utc_datetime(row["updated_at"]),
            last_triggered_at=utc_datetime(row["last_triggered_at"]),
            last_execution_id=(
                str(row["last_execution_id"])
                if row["last_execution_id"] is not None
                else None
            ),
            last_outcome=AutomationScheduleOutcome(str(row["last_outcome"])),
            last_error=(
                str(row["last_error"])
                if row["last_error"] is not None
                else None
            ),
        )

    @staticmethod
    def _run(row) -> AutomationScheduleRun:
        return AutomationScheduleRun(
            run_id=int(row["run_id"]),
            schedule_id=int(row["schedule_id"]),
            scheduled_for=utc_datetime(row["scheduled_for"]),
            claimed_at=utc_datetime(row["claimed_at"]),
            completed_at=utc_datetime(row["completed_at"]),
            status=AutomationScheduleRunStatus(str(row["status"])),
            execution_id=(
                str(row["execution_id"])
                if row["execution_id"] is not None
                else None
            ),
            resolved_payload=dict(row["resolved_payload"] or {}),
            error_message=(
                str(row["error_message"])
                if row["error_message"] is not None
                else None
            ),
        )


SQLiteAutomationScheduleRepository = SQLAutomationScheduleRepository
