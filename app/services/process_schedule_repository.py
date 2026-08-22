"""PostgreSQL persistence for Planning Process schedules."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database.engine import (
    DatabaseTarget,
    database_for,
    utc_datetime,
)
from app.infrastructure.database.schema import planning_processes, process_schedules
from app.models.process_schedule import (
    ProcessSchedule,
    ProcessScheduleInput,
    ScheduleContextMode,
    ScheduleFrequency,
    ScheduleRunOutcome,
)
from app.utils.exceptions import ScheduleError


class SQLProcessScheduleRepository:
    """Persist schedules and atomically claim due occurrences."""

    def __init__(self, database_target: DatabaseTarget) -> None:
        self._database = database_for(database_target)

    def create(
        self,
        schedule_input: ProcessScheduleInput,
        *,
        next_run_at: datetime | None,
        now: datetime,
    ) -> ProcessSchedule:
        try:
            with self._database.begin() as connection:
                self._ensure_process(connection, schedule_input.process_code, now)
                schedule_id = connection.execute(
                    insert(process_schedules)
                    .values(
                        name=schedule_input.name,
                        process_code=schedule_input.process_code,
                        frequency=schedule_input.frequency.value,
                        timezone=schedule_input.timezone,
                        first_run_local=schedule_input.first_run_local,
                        context_mode=schedule_input.context_mode.value,
                        run_profile_id=schedule_input.preset_id,
                        is_enabled=schedule_input.enabled,
                        next_run_at=next_run_at,
                        created_at=now,
                        updated_at=now,
                        last_outcome=ScheduleRunOutcome.NEVER.value,
                    )
                    .returning(process_schedules.c.schedule_id)
                ).scalar_one()
        except IntegrityError as exc:
            raise ScheduleError(
                f"An active schedule named '{schedule_input.name}' already exists."
            ) from exc
        schedule = self.get(int(schedule_id))
        if schedule is None:
            raise ScheduleError("The new schedule could not be retrieved.")
        return schedule

    def update(
        self,
        schedule_id: int,
        schedule_input: ProcessScheduleInput,
        *,
        next_run_at: datetime | None,
        now: datetime,
    ) -> ProcessSchedule:
        try:
            with self._database.begin() as connection:
                self._ensure_process(connection, schedule_input.process_code, now)
                result = connection.execute(
                    update(process_schedules)
                    .where(
                        process_schedules.c.schedule_id == schedule_id,
                        process_schedules.c.archived_at.is_(None),
                    )
                    .values(
                        name=schedule_input.name,
                        process_code=schedule_input.process_code,
                        frequency=schedule_input.frequency.value,
                        timezone=schedule_input.timezone,
                        first_run_local=schedule_input.first_run_local,
                        context_mode=schedule_input.context_mode.value,
                        run_profile_id=schedule_input.preset_id,
                        is_enabled=schedule_input.enabled,
                        next_run_at=next_run_at,
                        updated_at=now,
                        last_error=None,
                    )
                )
        except IntegrityError as exc:
            raise ScheduleError(
                f"An active schedule named '{schedule_input.name}' already exists."
            ) from exc
        if result.rowcount != 1:
            raise ScheduleError(f"Schedule {schedule_id} was not found.")
        schedule = self.get(schedule_id)
        if schedule is None:
            raise ScheduleError(f"Schedule {schedule_id} was not found.")
        return schedule

    def get(self, schedule_id: int) -> ProcessSchedule | None:
        with self._database.connect() as connection:
            row = connection.execute(
                select(process_schedules).where(
                    process_schedules.c.schedule_id == schedule_id,
                    process_schedules.c.archived_at.is_(None),
                )
            ).mappings().one_or_none()
        return self._schedule(row) if row is not None else None

    def list_active(self) -> tuple[ProcessSchedule, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                select(process_schedules)
                .where(process_schedules.c.archived_at.is_(None))
                .order_by(
                    process_schedules.c.is_enabled.desc(),
                    case((process_schedules.c.next_run_at.is_(None), 1), else_=0),
                    process_schedules.c.next_run_at,
                    func.lower(process_schedules.c.name),
                )
            ).mappings().all()
        return tuple(self._schedule(row) for row in rows)

    def list_due(self, now: datetime) -> tuple[ProcessSchedule, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                select(process_schedules)
                .where(
                    process_schedules.c.archived_at.is_(None),
                    process_schedules.c.is_enabled.is_(True),
                    process_schedules.c.next_run_at.is_not(None),
                    process_schedules.c.next_run_at <= now,
                )
                .order_by(
                    process_schedules.c.next_run_at,
                    process_schedules.c.schedule_id,
                )
            ).mappings().all()
        return tuple(self._schedule(row) for row in rows)

    def claim(
        self,
        schedule: ProcessSchedule,
        *,
        next_run_at: datetime | None,
        triggered_at: datetime,
    ) -> bool:
        with self._database.begin() as connection:
            result = connection.execute(
                update(process_schedules)
                .where(
                    process_schedules.c.schedule_id == schedule.schedule_id,
                    process_schedules.c.archived_at.is_(None),
                    process_schedules.c.is_enabled.is_(True),
                    process_schedules.c.next_run_at == schedule.next_run_at,
                )
                .values(
                    next_run_at=next_run_at,
                    is_enabled=next_run_at is not None,
                    last_triggered_at=triggered_at,
                    updated_at=triggered_at,
                    last_error=None,
                )
            )
        return result.rowcount == 1

    def set_enabled(
        self,
        schedule_id: int,
        *,
        enabled: bool,
        next_run_at: datetime | None,
        now: datetime,
    ) -> ProcessSchedule:
        with self._database.begin() as connection:
            result = connection.execute(
                update(process_schedules)
                .where(
                    process_schedules.c.schedule_id == schedule_id,
                    process_schedules.c.archived_at.is_(None),
                )
                .values(
                    is_enabled=enabled,
                    next_run_at=next_run_at,
                    updated_at=now,
                )
            )
        if result.rowcount != 1:
            raise ScheduleError(f"Schedule {schedule_id} was not found.")
        schedule = self.get(schedule_id)
        if schedule is None:
            raise ScheduleError(f"Schedule {schedule_id} was not found.")
        return schedule

    def record_result(
        self,
        schedule_id: int,
        *,
        outcome: ScheduleRunOutcome,
        execution_id: str | None,
        error: str | None,
        now: datetime,
        triggered_at: datetime | None = None,
    ) -> None:
        values: dict[str, object] = {
            "last_outcome": outcome.value,
            "last_execution_id": execution_id,
            "last_error": error,
            "updated_at": now,
        }
        if triggered_at is not None:
            values["last_triggered_at"] = triggered_at
        with self._database.begin() as connection:
            connection.execute(
                update(process_schedules)
                .where(
                    process_schedules.c.schedule_id == schedule_id,
                    process_schedules.c.archived_at.is_(None),
                )
                .values(**values)
            )

    def archive(self, schedule_id: int, *, now: datetime) -> None:
        with self._database.begin() as connection:
            result = connection.execute(
                update(process_schedules)
                .where(
                    process_schedules.c.schedule_id == schedule_id,
                    process_schedules.c.archived_at.is_(None),
                )
                .values(
                    archived_at=now,
                    is_enabled=False,
                    next_run_at=None,
                    updated_at=now,
                )
            )
        if result.rowcount != 1:
            raise ScheduleError(f"Schedule {schedule_id} was not found.")

    @staticmethod
    def _ensure_process(connection, process_code: str, now: datetime) -> None:
        exists = connection.execute(
            select(planning_processes.c.process_code).where(
                func.lower(planning_processes.c.process_code)
                == process_code.casefold()
            )
        ).scalar_one_or_none()
        if exists is None:
            connection.execute(
                insert(planning_processes).values(
                    process_code=process_code,
                    created_at=now,
                    updated_at=now,
                )
            )

    @staticmethod
    def _schedule(row) -> ProcessSchedule:
        return ProcessSchedule(
            schedule_id=int(row["schedule_id"]),
            name=str(row["name"]),
            process_code=str(row["process_code"]),
            frequency=ScheduleFrequency(str(row["frequency"])),
            timezone=str(row["timezone"]),
            first_run_local=row["first_run_local"],
            context_mode=ScheduleContextMode(str(row["context_mode"])),
            preset_id=(
                int(row["run_profile_id"])
                if row["run_profile_id"] is not None
                else None
            ),
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
            last_outcome=ScheduleRunOutcome(str(row["last_outcome"])),
            last_error=(
                str(row["last_error"])
                if row["last_error"] is not None
                else None
            ),
        )


SQLiteProcessScheduleRepository = SQLProcessScheduleRepository
