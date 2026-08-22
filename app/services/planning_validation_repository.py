"""Persistence adapter for summary-only Planning validation evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select, update

from app.infrastructure.database.engine import DatabaseTarget, database_for, utc_datetime
from app.infrastructure.database.schema import planning_task_validations
from app.models.planning_validation import (
    PlanningValidationEvidence,
    PlanningValidationStatus,
    PlanningValidationType,
)
from app.utils.exceptions import PlanningWorkflowError


class PlanningValidationRepository:
    """Store validation summaries while Oracle remains the data system of record."""

    def __init__(self, database_target: DatabaseTarget) -> None:
        self._database = database_for(database_target)

    def record(
        self,
        *,
        task_id: int,
        validation_type: PlanningValidationType,
        status: PlanningValidationStatus,
        source_cube: str,
        target_cube: str | None,
        selection: dict[str, Any],
        criteria: dict[str, Any],
        checked_cells: int,
        matched_cells: int | None,
        exception_count: int,
        warning_count: int,
        performed_by_user_id: int,
    ) -> PlanningValidationEvidence:
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            validation_id = int(
                connection.execute(
                    insert(planning_task_validations)
                    .values(
                        task_id=task_id,
                        validation_type=validation_type.value,
                        status=status.value,
                        source_cube=source_cube,
                        target_cube=target_cube,
                        selection=selection,
                        criteria=criteria,
                        checked_cells=checked_cells,
                        matched_cells=matched_cells,
                        exception_count=exception_count,
                        warning_count=warning_count,
                        performed_by_user_id=performed_by_user_id,
                        performed_at=now,
                    )
                    .returning(planning_task_validations.c.validation_id)
                ).scalar_one()
            )
        return self.require(validation_id)

    def latest_for_task(self, task_id: int) -> PlanningValidationEvidence | None:
        with self._database.connect() as connection:
            row = connection.execute(
                select(planning_task_validations)
                .where(planning_task_validations.c.task_id == task_id)
                .order_by(
                    planning_task_validations.c.performed_at.desc(),
                    planning_task_validations.c.validation_id.desc(),
                )
                .limit(1)
            ).mappings().one_or_none()
        return self._evidence(row) if row is not None else None

    def latest_for_tasks(
        self, task_ids: tuple[int, ...]
    ) -> dict[int, PlanningValidationEvidence]:
        return {
            task_id: evidence
            for task_id in task_ids
            if (evidence := self.latest_for_task(task_id)) is not None
        }

    def acknowledge_warning(
        self, validation_id: int, *, user_id: int
    ) -> PlanningValidationEvidence:
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            row = connection.execute(
                select(planning_task_validations)
                .where(planning_task_validations.c.validation_id == validation_id)
                .with_for_update()
            ).mappings().one_or_none()
            if row is None:
                raise PlanningWorkflowError(
                    f"Planning validation {validation_id} was not found."
                )
            if row["status"] != PlanningValidationStatus.WARNING.value:
                raise PlanningWorkflowError(
                    "Only a validation with warnings requires acknowledgement."
                )
            connection.execute(
                update(planning_task_validations)
                .where(planning_task_validations.c.validation_id == validation_id)
                .values(
                    warning_acknowledged_by_user_id=user_id,
                    warning_acknowledged_at=now,
                )
            )
        return self.require(validation_id)

    def require(self, validation_id: int) -> PlanningValidationEvidence:
        with self._database.connect() as connection:
            row = connection.execute(
                select(planning_task_validations).where(
                    planning_task_validations.c.validation_id == validation_id
                )
            ).mappings().one_or_none()
        if row is None:
            raise PlanningWorkflowError(
                f"Planning validation {validation_id} was not found."
            )
        return self._evidence(row)

    @staticmethod
    def _evidence(row) -> PlanningValidationEvidence:
        return PlanningValidationEvidence(
            validation_id=int(row["validation_id"]),
            task_id=int(row["task_id"]),
            validation_type=PlanningValidationType(str(row["validation_type"])),
            status=PlanningValidationStatus(str(row["status"])),
            source_cube=str(row["source_cube"]),
            target_cube=row["target_cube"],
            selection=dict(row["selection"] or {}),
            criteria=dict(row["criteria"] or {}),
            checked_cells=int(row["checked_cells"]),
            matched_cells=(
                int(row["matched_cells"])
                if row["matched_cells"] is not None
                else None
            ),
            exception_count=int(row["exception_count"]),
            warning_count=int(row["warning_count"]),
            performed_by_user_id=int(row["performed_by_user_id"]),
            performed_at=utc_datetime(row["performed_at"]),
            warning_acknowledged_by_user_id=(
                int(row["warning_acknowledged_by_user_id"])
                if row["warning_acknowledged_by_user_id"] is not None
                else None
            ),
            warning_acknowledged_at=utc_datetime(row["warning_acknowledged_at"]),
        )
