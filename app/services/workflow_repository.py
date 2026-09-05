"""Durable workflow history backed by SQLAlchemy/PostgreSQL."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import delete, insert, select

from app.infrastructure.database.engine import (
    DatabaseTarget,
    database_for,
    upsert_statement,
    utc_datetime,
)
from app.infrastructure.database.schema import workflow_runs, workflow_steps
from app.models.access_control import TriggerSource
from app.models.workflow import (
    WorkflowRun,
    WorkflowStatus,
    WorkflowStepResult,
    WorkflowStepStatus,
)


class WorkflowRepository(Protocol):
    """Persistence contract used by the workflow engine."""

    def save(self, run: WorkflowRun) -> None: ...

    def get(self, execution_id: str) -> WorkflowRun | None: ...

    def list_recent(self, *, limit: int = 20) -> tuple[WorkflowRun, ...]: ...


class SQLWorkflowRepository:
    """Persist complete workflow aggregates in one transaction."""

    def __init__(self, database_target: DatabaseTarget) -> None:
        self._database = database_for(database_target)

    def save(self, run: WorkflowRun) -> None:
        values = {
            "execution_id": run.execution_id,
            "workflow_name": run.workflow_name,
            "status": run.status.value,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "error_message": run.error_message,
            "initiated_by_username": run.initiated_by,
            "initiated_by_display": run.initiated_by_display,
            "trigger_source": run.trigger_source.value,
            "oracle_execution_username": run.oracle_execution_username,
        }
        with self._database.begin() as connection:
            connection.execute(
                upsert_statement(
                    connection,
                    workflow_runs,
                    values,
                    index_elements=("execution_id",),
                    update_columns=tuple(
                        key for key in values if key != "execution_id"
                    ),
                )
            )
            connection.execute(
                delete(workflow_steps).where(
                    workflow_steps.c.execution_id == run.execution_id
                )
            )
            if run.steps:
                connection.execute(
                    insert(workflow_steps),
                    [
                        {
                            "execution_id": run.execution_id,
                            "sequence": step.sequence,
                            "name": step.name,
                            "status": step.status.value,
                            "started_at": step.started_at,
                            "completed_at": step.completed_at,
                            "details": step.details,
                            "error_message": step.error_message,
                        }
                        for step in run.steps
                    ],
                )

    def get(self, execution_id: str) -> WorkflowRun | None:
        with self._database.connect() as connection:
            row = connection.execute(
                select(workflow_runs).where(
                    workflow_runs.c.execution_id == execution_id
                )
            ).mappings().one_or_none()
            if row is None:
                return None
            step_rows = connection.execute(
                select(workflow_steps)
                .where(workflow_steps.c.execution_id == execution_id)
                .order_by(workflow_steps.c.sequence)
            ).mappings().all()
        return WorkflowRun(
            execution_id=str(row["execution_id"]),
            workflow_name=str(row["workflow_name"]),
            status=WorkflowStatus(str(row["status"])),
            started_at=utc_datetime(row["started_at"]),
            completed_at=utc_datetime(row["completed_at"]),
            error_message=row["error_message"],
            initiated_by=row["initiated_by_username"],
            initiated_by_display=row["initiated_by_display"],
            trigger_source=TriggerSource(str(row["trigger_source"])),
            oracle_execution_username=row["oracle_execution_username"],
            steps=tuple(
                WorkflowStepResult(
                    name=str(step["name"]),
                    sequence=int(step["sequence"]),
                    status=WorkflowStepStatus(str(step["status"])),
                    started_at=utc_datetime(step["started_at"]),
                    completed_at=utc_datetime(step["completed_at"]),
                    details=dict(step["details"] or {}),
                    error_message=step["error_message"],
                )
                for step in step_rows
            ),
        )

    def list_recent(self, *, limit: int = 20) -> tuple[WorkflowRun, ...]:
        if limit <= 0:
            raise ValueError("limit must be greater than zero.")
        with self._database.connect() as connection:
            identifiers = connection.execute(
                select(workflow_runs.c.execution_id)
                .order_by(workflow_runs.c.started_at.desc())
                .limit(limit)
            ).scalars().all()
        return tuple(
            run
            for execution_id in identifiers
            if (run := self.get(str(execution_id))) is not None
        )


# Temporary import compatibility for extensions built against the prototype.
SQLiteWorkflowRepository = SQLWorkflowRepository
