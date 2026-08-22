"""PostgreSQL repository for operational Planning cycles and tasks."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import and_, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database.engine import (
    DatabaseTarget,
    database_for,
    utc_datetime,
)
from app.infrastructure.database.schema import (
    planning_cycle_stages,
    planning_cycles,
    planning_task_dependencies,
    planning_task_executions,
    planning_tasks,
    platform_roles,
    platform_users,
    workflow_runs,
)
from app.models.access_control import UserAccount
from app.models.planning_workflow import (
    CycleStageStatus,
    OperationalCycleStatus,
    PlanningCycleDraft,
    PlanningCycleRecord,
    PlanningCycleStage,
    PlanningTask,
    PlanningTaskExecution,
    PlanningTaskExecutionStatus,
    PlanningTaskPriority,
    PlanningTaskStatus,
)
from app.utils.exceptions import PlanningWorkflowError


class PlanningWorkRepository:
    """Persist complete cycle aggregates with explicit transaction boundaries."""

    def __init__(self, database_target: DatabaseTarget) -> None:
        self._database = database_for(database_target)

    def create_cycle(
        self,
        draft: PlanningCycleDraft,
        *,
        created_by_user_id: int,
    ) -> PlanningCycleRecord:
        now = datetime.now(UTC)
        try:
            with self._database.begin() as connection:
                cycle_id = int(
                    connection.execute(
                        insert(planning_cycles)
                        .values(
                            code=draft.code,
                            name=draft.name,
                            cycle_type=draft.cycle_type,
                            process_code=draft.process_code,
                            scenario=draft.scenario,
                            year=draft.year,
                            actual_through_period=draft.actual_through_period,
                            forecast_start_period=draft.forecast_start_period,
                            start_date=draft.start_date,
                            due_date=draft.due_date,
                            status=OperationalCycleStatus.OPEN.value,
                            created_by_user_id=created_by_user_id,
                            created_at=now,
                            updated_at=now,
                        )
                        .returning(planning_cycles.c.cycle_id)
                    ).scalar_one()
                )
                stage_ids: dict[str, int] = {}
                for stage in draft.stages:
                    stage_id = int(
                        connection.execute(
                            insert(planning_cycle_stages)
                            .values(
                                cycle_id=cycle_id,
                                sequence=stage.sequence,
                                code=stage.code,
                                name=stage.name,
                                status=CycleStageStatus.NOT_STARTED.value,
                                start_date=stage.start_date,
                                due_date=stage.due_date,
                            )
                            .returning(planning_cycle_stages.c.stage_id)
                        ).scalar_one()
                    )
                    stage_ids[stage.code.casefold()] = stage_id

                task_ids: dict[str, int] = {}
                for task in draft.tasks:
                    user_id, role_id = self._resolve_assignee(
                        connection,
                        username=task.assigned_username,
                        role_code=task.assigned_role_code,
                    )
                    task_id = int(
                        connection.execute(
                            insert(planning_tasks)
                            .values(
                                stage_id=stage_ids[task.stage_code.casefold()],
                                title=task.title,
                                description=task.description,
                                task_type=task.task_type,
                                status=PlanningTaskStatus.NOT_STARTED.value,
                                priority=task.priority.value,
                                assigned_user_id=user_id,
                                assigned_role_id=role_id,
                                entity=task.entity,
                                scenario=task.scenario,
                                period=task.period,
                                due_at=task.due_at,
                                action_type=task.action_type,
                                action_config=task.action_config,
                                created_by_user_id=created_by_user_id,
                                created_at=now,
                                updated_at=now,
                            )
                            .returning(planning_tasks.c.task_id)
                        ).scalar_one()
                    )
                    task_ids[task.key.casefold()] = task_id

                dependencies = [
                    {
                        "task_id": task_ids[task.key.casefold()],
                        "depends_on_task_id": task_ids[key.casefold()],
                    }
                    for task in draft.tasks
                    for key in task.depends_on
                ]
                if dependencies:
                    connection.execute(
                        insert(planning_task_dependencies), dependencies
                    )
        except IntegrityError as exc:
            raise PlanningWorkflowError(
                "The Planning cycle could not be created because its code, "
                "process, user, role, or stage configuration is invalid."
            ) from exc
        return self.require_cycle(cycle_id)

    def list_cycles(self) -> tuple[PlanningCycleRecord, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                select(planning_cycles)
                .where(planning_cycles.c.archived_at.is_(None))
                .order_by(
                    planning_cycles.c.due_date,
                    func.lower(planning_cycles.c.name),
                )
            ).mappings().all()
        return tuple(self._cycle(row) for row in rows)

    def require_cycle(self, cycle_id: int) -> PlanningCycleRecord:
        with self._database.connect() as connection:
            row = connection.execute(
                select(planning_cycles).where(
                    planning_cycles.c.cycle_id == cycle_id,
                    planning_cycles.c.archived_at.is_(None),
                )
            ).mappings().one_or_none()
        if row is None:
            raise PlanningWorkflowError(
                f"Planning cycle {cycle_id} was not found."
            )
        return self._cycle(row)

    def list_stages(self, cycle_id: int) -> tuple[PlanningCycleStage, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                select(planning_cycle_stages)
                .where(planning_cycle_stages.c.cycle_id == cycle_id)
                .order_by(planning_cycle_stages.c.sequence)
            ).mappings().all()
        return tuple(self._stage(row) for row in rows)

    def list_tasks_for_user(
        self,
        user: UserAccount,
        *,
        cycle_id: int | None = None,
    ) -> tuple[PlanningTask, ...]:
        role_codes = [role.value for role in user.roles]
        with self._database.connect() as connection:
            filters = [
                or_(
                    planning_tasks.c.assigned_user_id == user.user_id,
                    platform_roles.c.code.in_(role_codes),
                )
            ]
            if cycle_id is not None:
                filters.append(planning_cycles.c.cycle_id == cycle_id)
            rows = connection.execute(
                self._task_select()
                .where(and_(*filters))
                .order_by(
                    planning_cycles.c.due_date,
                    planning_cycle_stages.c.sequence,
                    planning_tasks.c.due_at.nulls_last(),
                    planning_tasks.c.task_id,
                )
            ).mappings().all()
            return self._tasks_with_dependencies(connection, rows)

    def list_tasks(self, cycle_id: int) -> tuple[PlanningTask, ...]:
        """Return every task in a cycle for an authorized designer."""
        with self._database.connect() as connection:
            rows = connection.execute(
                self._task_select()
                .where(planning_cycles.c.cycle_id == cycle_id)
                .order_by(
                    planning_cycle_stages.c.sequence,
                    planning_tasks.c.due_at.nulls_last(),
                    planning_tasks.c.task_id,
                )
            ).mappings().all()
            return self._tasks_with_dependencies(connection, rows)

    def require_task(self, task_id: int) -> PlanningTask:
        with self._database.connect() as connection:
            row = connection.execute(
                self._task_select().where(planning_tasks.c.task_id == task_id)
            ).mappings().one_or_none()
            if row is None:
                raise PlanningWorkflowError(
                    f"Planning task {task_id} was not found."
                )
            return self._tasks_with_dependencies(connection, [row])[0]

    def update_task_status(
        self,
        task_id: int,
        status: PlanningTaskStatus,
    ) -> PlanningTask:
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            row = connection.execute(
                select(planning_tasks.c.stage_id)
                .where(planning_tasks.c.task_id == task_id)
                .with_for_update()
            ).one_or_none()
            if row is None:
                raise PlanningWorkflowError(
                    f"Planning task {task_id} was not found."
                )
            stage_id = int(row.stage_id)
            completed_at = (
                now if status is PlanningTaskStatus.COMPLETED else None
            )
            connection.execute(
                update(planning_tasks)
                .where(planning_tasks.c.task_id == task_id)
                .values(
                    status=status.value,
                    completed_at=completed_at,
                    updated_at=now,
                )
            )
            cycle_id = int(
                connection.execute(
                    select(planning_cycle_stages.c.cycle_id).where(
                        planning_cycle_stages.c.stage_id == stage_id
                    )
                ).scalar_one()
            )
            self._recalculate_progress(connection, stage_id, cycle_id, now)
        return self.require_task(task_id)

    def link_task_execution(
        self,
        task_id: int,
        execution_id: str,
        *,
        initiated_by_user_id: int,
    ) -> PlanningTaskExecution:
        """Create the next immutable attempt and move its task in progress."""
        now = datetime.now(UTC)
        try:
            with self._database.begin() as connection:
                task_row = connection.execute(
                    select(planning_tasks.c.stage_id)
                    .where(planning_tasks.c.task_id == task_id)
                    .with_for_update()
                ).one_or_none()
                if task_row is None:
                    raise PlanningWorkflowError(
                        f"Planning task {task_id} was not found."
                    )
                existing = connection.execute(
                    select(planning_task_executions).where(
                        planning_task_executions.c.execution_id == execution_id
                    )
                ).mappings().one_or_none()
                if existing is not None:
                    if int(existing["task_id"]) != task_id:
                        raise PlanningWorkflowError(
                            "This Oracle execution is already linked to another task."
                        )
                    return self._task_execution(existing)
                attempt_number = int(
                    connection.execute(
                        select(
                            func.coalesce(
                                func.max(
                                    planning_task_executions.c.attempt_number
                                ),
                                0,
                            )
                        ).where(planning_task_executions.c.task_id == task_id)
                    ).scalar_one()
                ) + 1
                task_execution_id = int(
                    connection.execute(
                        insert(planning_task_executions)
                        .values(
                            task_id=task_id,
                            execution_id=execution_id,
                            attempt_number=attempt_number,
                            status=PlanningTaskExecutionStatus.QUEUED.value,
                            initiated_by_user_id=initiated_by_user_id,
                            linked_at=now,
                            updated_at=now,
                        )
                        .returning(
                            planning_task_executions.c.task_execution_id
                        )
                    ).scalar_one()
                )
                connection.execute(
                    update(planning_tasks)
                    .where(planning_tasks.c.task_id == task_id)
                    .values(
                        status=PlanningTaskStatus.IN_PROGRESS.value,
                        completed_at=None,
                        updated_at=now,
                    )
                )
                stage_id = int(task_row.stage_id)
                cycle_id = int(
                    connection.execute(
                        select(planning_cycle_stages.c.cycle_id).where(
                            planning_cycle_stages.c.stage_id == stage_id
                        )
                    ).scalar_one()
                )
                self._recalculate_progress(connection, stage_id, cycle_id, now)
                linked = connection.execute(
                    select(planning_task_executions).where(
                        planning_task_executions.c.task_execution_id
                        == task_execution_id
                    )
                ).mappings().one()
                return self._task_execution(linked)
        except IntegrityError as exc:
            raise PlanningWorkflowError(
                "The Oracle execution could not be linked to this task."
            ) from exc

    def reconcile_task_executions(self) -> None:
        """Project final workflow outcomes onto their latest business task."""
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            active_rows = connection.execute(
                select(
                    planning_task_executions,
                    workflow_runs.c.status.label("workflow_status"),
                    workflow_runs.c.completed_at.label("workflow_completed_at"),
                    workflow_runs.c.error_message.label("workflow_error"),
                )
                .outerjoin(
                    workflow_runs,
                    workflow_runs.c.execution_id
                    == planning_task_executions.c.execution_id,
                )
                .where(
                    planning_task_executions.c.status.in_(
                        (
                            PlanningTaskExecutionStatus.QUEUED.value,
                            PlanningTaskExecutionStatus.RUNNING.value,
                        )
                    )
                )
                .order_by(
                    planning_task_executions.c.task_id,
                    planning_task_executions.c.attempt_number,
                )
            ).mappings().all()
            for row in active_rows:
                workflow_status = row["workflow_status"]
                if workflow_status not in {"RUNNING", "SUCCESS", "FAILED"}:
                    continue
                execution_status = PlanningTaskExecutionStatus(workflow_status)
                connection.execute(
                    update(planning_task_executions)
                    .where(
                        planning_task_executions.c.task_execution_id
                        == row["task_execution_id"]
                    )
                    .values(
                        status=execution_status.value,
                        updated_at=now,
                        completed_at=(
                            row["workflow_completed_at"]
                            if execution_status
                            in {
                                PlanningTaskExecutionStatus.SUCCESS,
                                PlanningTaskExecutionStatus.FAILED,
                            }
                            else None
                        ),
                        error_message=(
                            row["workflow_error"]
                            if execution_status
                            is PlanningTaskExecutionStatus.FAILED
                            else None
                        ),
                    )
                )
                latest_attempt = connection.execute(
                    select(func.max(planning_task_executions.c.attempt_number)).where(
                        planning_task_executions.c.task_id == row["task_id"]
                    )
                ).scalar_one()
                if int(latest_attempt) != int(row["attempt_number"]):
                    continue
                task_status = {
                    PlanningTaskExecutionStatus.RUNNING: PlanningTaskStatus.IN_PROGRESS,
                    PlanningTaskExecutionStatus.SUCCESS: PlanningTaskStatus.COMPLETED,
                    PlanningTaskExecutionStatus.FAILED: PlanningTaskStatus.BLOCKED,
                }[execution_status]
                task_row = connection.execute(
                    select(planning_tasks.c.stage_id).where(
                        planning_tasks.c.task_id == row["task_id"]
                    )
                ).one_or_none()
                if task_row is None:
                    continue
                connection.execute(
                    update(planning_tasks)
                    .where(planning_tasks.c.task_id == row["task_id"])
                    .values(
                        status=task_status.value,
                        completed_at=(
                            row["workflow_completed_at"]
                            if task_status is PlanningTaskStatus.COMPLETED
                            else None
                        ),
                        updated_at=now,
                    )
                )
                stage_id = int(task_row.stage_id)
                cycle_id = int(
                    connection.execute(
                        select(planning_cycle_stages.c.cycle_id).where(
                            planning_cycle_stages.c.stage_id == stage_id
                        )
                    ).scalar_one()
                )
                self._recalculate_progress(connection, stage_id, cycle_id, now)

    def list_task_executions(
        self,
        task_ids: tuple[int, ...],
    ) -> dict[int, tuple[PlanningTaskExecution, ...]]:
        """Return attempt history for several task cards in one query."""
        if not task_ids:
            return {}
        with self._database.connect() as connection:
            rows = connection.execute(
                select(planning_task_executions)
                .where(planning_task_executions.c.task_id.in_(task_ids))
                .order_by(
                    planning_task_executions.c.task_id,
                    planning_task_executions.c.attempt_number.desc(),
                )
            ).mappings().all()
        grouped: dict[int, list[PlanningTaskExecution]] = defaultdict(list)
        for row in rows:
            grouped[int(row["task_id"])].append(self._task_execution(row))
        return {task_id: tuple(items) for task_id, items in grouped.items()}

    def task_id_for_execution(self, execution_id: str) -> int | None:
        """Resolve a retained execution to its owning business task."""
        with self._database.connect() as connection:
            task_id = connection.execute(
                select(planning_task_executions.c.task_id).where(
                    planning_task_executions.c.execution_id == execution_id
                )
            ).scalar_one_or_none()
        return int(task_id) if task_id is not None else None

    @staticmethod
    def _resolve_assignee(
        connection,
        *,
        username: str | None,
        role_code: str | None,
    ) -> tuple[int | None, int | None]:
        if username:
            user_id = connection.execute(
                select(platform_users.c.user_id).where(
                    func.lower(platform_users.c.username)
                    == username.casefold(),
                    platform_users.c.is_active.is_(True),
                )
            ).scalar_one_or_none()
            if user_id is None:
                raise PlanningWorkflowError(
                    f"Active platform user '{username}' was not found."
                )
            return int(user_id), None
        role_id = connection.execute(
            select(platform_roles.c.role_id).where(
                func.lower(platform_roles.c.code)
                == str(role_code).casefold()
            )
        ).scalar_one_or_none()
        if role_id is None:
            raise PlanningWorkflowError(
                f"Platform role '{role_code}' was not found."
            )
        return None, int(role_id)

    @staticmethod
    def _task_select():
        return (
            select(
                planning_tasks,
                planning_cycle_stages.c.cycle_id.label("cycle_id"),
                planning_cycle_stages.c.code.label("stage_code"),
                planning_cycle_stages.c.name.label("stage_name"),
                planning_cycles.c.code.label("cycle_code"),
                planning_cycles.c.name.label("cycle_name"),
                platform_roles.c.code.label("assigned_role_code"),
            )
            .join(
                planning_cycle_stages,
                planning_cycle_stages.c.stage_id == planning_tasks.c.stage_id,
            )
            .join(
                planning_cycles,
                planning_cycles.c.cycle_id == planning_cycle_stages.c.cycle_id,
            )
            .outerjoin(
                platform_roles,
                platform_roles.c.role_id == planning_tasks.c.assigned_role_id,
            )
            .where(planning_cycles.c.archived_at.is_(None))
        )

    def _tasks_with_dependencies(self, connection, rows) -> tuple[PlanningTask, ...]:
        if not rows:
            return ()
        task_ids = [int(row["task_id"]) for row in rows]
        prerequisite = planning_tasks.alias("prerequisite")
        dependency_rows = connection.execute(
            select(
                planning_task_dependencies.c.task_id,
                planning_task_dependencies.c.depends_on_task_id,
                prerequisite.c.status,
            )
            .join(
                prerequisite,
                prerequisite.c.task_id
                == planning_task_dependencies.c.depends_on_task_id,
            )
            .where(planning_task_dependencies.c.task_id.in_(task_ids))
        ).all()
        dependencies: dict[int, list[int]] = defaultdict(list)
        incomplete: dict[int, list[int]] = defaultdict(list)
        for row in dependency_rows:
            task_id = int(row.task_id)
            dependency_id = int(row.depends_on_task_id)
            dependencies[task_id].append(dependency_id)
            if row.status != PlanningTaskStatus.COMPLETED.value:
                incomplete[task_id].append(dependency_id)
        return tuple(
            self._task(
                row,
                dependency_ids=tuple(dependencies[int(row["task_id"])]),
                incomplete_dependency_ids=tuple(
                    incomplete[int(row["task_id"])]
                ),
            )
            for row in rows
        )

    @staticmethod
    def _recalculate_progress(connection, stage_id: int, cycle_id: int, now: datetime) -> None:
        task_statuses = tuple(
            connection.execute(
                select(planning_tasks.c.status).where(
                    planning_tasks.c.stage_id == stage_id
                )
            ).scalars()
        )
        terminal = {
            PlanningTaskStatus.COMPLETED.value,
            PlanningTaskStatus.CANCELLED.value,
        }
        if task_statuses and all(value in terminal for value in task_statuses):
            stage_status = CycleStageStatus.COMPLETED
            stage_completed_at = now
        elif any(
            value
            in {
                PlanningTaskStatus.IN_PROGRESS.value,
                PlanningTaskStatus.COMPLETED.value,
            }
            for value in task_statuses
        ):
            stage_status = CycleStageStatus.IN_PROGRESS
            stage_completed_at = None
        elif task_statuses and all(
            value == PlanningTaskStatus.BLOCKED.value for value in task_statuses
        ):
            stage_status = CycleStageStatus.BLOCKED
            stage_completed_at = None
        else:
            stage_status = CycleStageStatus.NOT_STARTED
            stage_completed_at = None
        connection.execute(
            update(planning_cycle_stages)
            .where(planning_cycle_stages.c.stage_id == stage_id)
            .values(
                status=stage_status.value,
                completed_at=stage_completed_at,
            )
        )

        stage_statuses = tuple(
            connection.execute(
                select(planning_cycle_stages.c.status).where(
                    planning_cycle_stages.c.cycle_id == cycle_id
                )
            ).scalars()
        )
        if stage_statuses and all(
            value
            in {
                CycleStageStatus.COMPLETED.value,
                CycleStageStatus.SKIPPED.value,
            }
            for value in stage_statuses
        ):
            cycle_status = OperationalCycleStatus.COMPLETED
            cycle_completed_at = now
        elif any(
            value
            in {
                CycleStageStatus.IN_PROGRESS.value,
                CycleStageStatus.COMPLETED.value,
                CycleStageStatus.BLOCKED.value,
            }
            for value in stage_statuses
        ):
            cycle_status = OperationalCycleStatus.IN_PROGRESS
            cycle_completed_at = None
        else:
            cycle_status = OperationalCycleStatus.OPEN
            cycle_completed_at = None
        connection.execute(
            update(planning_cycles)
            .where(planning_cycles.c.cycle_id == cycle_id)
            .values(
                status=cycle_status.value,
                completed_at=cycle_completed_at,
                updated_at=now,
            )
        )

    @staticmethod
    def _cycle(row) -> PlanningCycleRecord:
        return PlanningCycleRecord(
            cycle_id=int(row["cycle_id"]),
            code=str(row["code"]),
            name=str(row["name"]),
            cycle_type=str(row["cycle_type"]),
            process_code=row["process_code"],
            scenario=row["scenario"],
            year=str(row["year"]),
            actual_through_period=row["actual_through_period"],
            forecast_start_period=row["forecast_start_period"],
            start_date=row["start_date"],
            due_date=row["due_date"],
            status=OperationalCycleStatus(str(row["status"])),
            created_by_user_id=int(row["created_by_user_id"]),
            created_at=utc_datetime(row["created_at"]),
            updated_at=utc_datetime(row["updated_at"]),
            completed_at=utc_datetime(row["completed_at"]),
        )

    @staticmethod
    def _stage(row) -> PlanningCycleStage:
        return PlanningCycleStage(
            stage_id=int(row["stage_id"]),
            cycle_id=int(row["cycle_id"]),
            sequence=int(row["sequence"]),
            code=str(row["code"]),
            name=str(row["name"]),
            status=CycleStageStatus(str(row["status"])),
            start_date=row["start_date"],
            due_date=row["due_date"],
            completed_at=utc_datetime(row["completed_at"]),
        )

    @staticmethod
    def _task(
        row,
        *,
        dependency_ids: tuple[int, ...],
        incomplete_dependency_ids: tuple[int, ...],
    ) -> PlanningTask:
        return PlanningTask(
            task_id=int(row["task_id"]),
            stage_id=int(row["stage_id"]),
            cycle_id=int(row["cycle_id"]),
            cycle_code=str(row["cycle_code"]),
            cycle_name=str(row["cycle_name"]),
            stage_code=str(row["stage_code"]),
            stage_name=str(row["stage_name"]),
            title=str(row["title"]),
            description=str(row["description"]),
            task_type=str(row["task_type"]),
            status=PlanningTaskStatus(str(row["status"])),
            priority=PlanningTaskPriority(str(row["priority"])),
            assigned_user_id=(
                int(row["assigned_user_id"])
                if row["assigned_user_id"] is not None
                else None
            ),
            assigned_role_code=row["assigned_role_code"],
            entity=row["entity"],
            scenario=row["scenario"],
            period=row["period"],
            due_at=utc_datetime(row["due_at"]),
            action_type=str(row["action_type"]),
            action_config=dict(row["action_config"] or {}),
            dependency_ids=dependency_ids,
            incomplete_dependency_ids=incomplete_dependency_ids,
            created_at=utc_datetime(row["created_at"]),
            updated_at=utc_datetime(row["updated_at"]),
            completed_at=utc_datetime(row["completed_at"]),
        )

    @staticmethod
    def _task_execution(row) -> PlanningTaskExecution:
        return PlanningTaskExecution(
            task_execution_id=int(row["task_execution_id"]),
            task_id=int(row["task_id"]),
            execution_id=str(row["execution_id"]),
            attempt_number=int(row["attempt_number"]),
            status=PlanningTaskExecutionStatus(str(row["status"])),
            initiated_by_user_id=int(row["initiated_by_user_id"]),
            linked_at=utc_datetime(row["linked_at"]),
            updated_at=utc_datetime(row["updated_at"]),
            completed_at=utc_datetime(row["completed_at"]),
            error_message=row["error_message"],
        )
