"""PostgreSQL-backed durable queue with explicit worker leases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database.engine import (
    DatabaseTarget,
    database_for,
    utc_datetime,
)
from app.infrastructure.database.schema import execution_queue
from app.models.execution_queue import (
    ExecutionJob,
    ExecutionJobStatus,
    ExecutionJobSubmission,
    ExecutionJobType,
)
from app.utils.exceptions import (
    ExecutionQueueConflictError,
    ExecutionQueueError,
)


class SQLExecutionQueueRepository:
    """Persist and atomically lease serializable Oracle work."""

    def __init__(self, database_target: DatabaseTarget) -> None:
        self._database = database_for(database_target)

    def enqueue(
        self,
        submission: ExecutionJobSubmission,
        *,
        now: datetime | None = None,
    ) -> ExecutionJob:
        current = _utc(now)
        values = {
            "execution_id": submission.execution_id,
            "job_type": submission.job_type.value,
            "target_key": submission.target_key,
            "payload": submission.payload,
            "status": ExecutionJobStatus.QUEUED.value,
            "priority": submission.priority,
            "attempt_count": 0,
            "created_at": current,
            "available_at": current,
        }
        try:
            with self._database.begin() as connection:
                connection.execute(execution_queue.insert().values(**values))
        except IntegrityError as exc:
            active = self.find_active(
                submission.job_type,
                submission.target_key,
            )
            if active is not None:
                raise ExecutionQueueConflictError(
                    f"Target '{submission.target_key}' already has an active "
                    f"execution: {active.execution_id}."
                ) from exc
            raise ExecutionQueueError(
                f"Execution '{submission.execution_id}' could not be queued."
            ) from exc
        job = self.get(submission.execution_id)
        if job is None:  # pragma: no cover - defensive database invariant.
            raise ExecutionQueueError("The queued execution could not be read back.")
        return job

    def get(self, execution_id: str) -> ExecutionJob | None:
        with self._database.connect() as connection:
            row = connection.execute(
                select(execution_queue).where(
                    execution_queue.c.execution_id == execution_id
                )
            ).mappings().one_or_none()
        return _job(row) if row is not None else None

    def find_active(
        self,
        job_type: ExecutionJobType,
        target_key: str,
    ) -> ExecutionJob | None:
        with self._database.connect() as connection:
            row = connection.execute(
                select(execution_queue)
                .where(
                    execution_queue.c.job_type == job_type.value,
                    func.lower(execution_queue.c.target_key)
                    == target_key.casefold(),
                    execution_queue.c.status.in_(
                        (
                            ExecutionJobStatus.QUEUED.value,
                            ExecutionJobStatus.RUNNING.value,
                        )
                    ),
                )
                .order_by(execution_queue.c.created_at)
                .limit(1)
            ).mappings().one_or_none()
        return _job(row) if row is not None else None

    def discard_queued(self, execution_id: str) -> bool:
        """Remove work that failed governance before a worker could claim it."""
        with self._database.begin() as connection:
            result = connection.execute(
                delete(execution_queue).where(
                    execution_queue.c.execution_id == execution_id,
                    execution_queue.c.status == ExecutionJobStatus.QUEUED.value,
                )
            )
        return result.rowcount == 1

    def request_cancellation(
        self,
        execution_id: str,
        *,
        requested_by: str,
        now: datetime | None = None,
    ) -> ExecutionJob:
        """Cancel queued work or request a safe stop after the active step."""
        current = _utc(now)
        actor = " ".join(str(requested_by or "").split())[:80] or "unknown"
        with self._database.begin() as connection:
            statement = select(execution_queue).where(
                execution_queue.c.execution_id == execution_id
            )
            if connection.dialect.name == "postgresql":
                statement = statement.with_for_update()
            row = connection.execute(statement).mappings().one_or_none()
            if row is None:
                raise ExecutionQueueError(
                    f"Execution '{execution_id}' was not found."
                )
            status = ExecutionJobStatus(str(row["status"]))
            if status.terminal:
                return _job(row)
            values: dict[str, object] = {
                "cancellation_requested_at": current,
                "cancellation_requested_by": actor,
            }
            if status is ExecutionJobStatus.QUEUED:
                values.update(
                    status=ExecutionJobStatus.CANCELLED.value,
                    completed_at=current,
                    error_message=(
                        "Cancelled before a worker started the execution."
                    ),
                )
            connection.execute(
                update(execution_queue)
                .where(execution_queue.c.execution_id == execution_id)
                .values(**values)
            )
            refreshed = connection.execute(statement).mappings().one()
        return _job(refreshed)

    def cancellation_requested(self, execution_id: str) -> bool:
        with self._database.connect() as connection:
            value = connection.execute(
                select(execution_queue.c.cancellation_requested_at).where(
                    execution_queue.c.execution_id == execution_id
                )
            ).scalar_one_or_none()
        return value is not None

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        job_types: tuple[ExecutionJobType, ...] | None = None,
        now: datetime | None = None,
    ) -> ExecutionJob | None:
        return self._claim(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            job_types=job_types,
            now=now,
        )

    def claim(
        self,
        execution_id: str,
        *,
        worker_id: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> ExecutionJob | None:
        return self._claim(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            execution_id=execution_id,
            now=now,
        )

    def _claim(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        execution_id: str | None = None,
        job_types: tuple[ExecutionJobType, ...] | None = None,
        now: datetime | None = None,
    ) -> ExecutionJob | None:
        current = _utc(now)
        lease_expires = current + timedelta(seconds=max(lease_seconds, 30))
        with self._database.begin() as connection:
            conditions = [
                execution_queue.c.status == ExecutionJobStatus.QUEUED.value,
                execution_queue.c.available_at <= current,
            ]
            if execution_id:
                conditions.append(execution_queue.c.execution_id == execution_id)
            if job_types:
                conditions.append(
                    execution_queue.c.job_type.in_(
                        tuple(item.value for item in job_types)
                    )
                )
            statement = (
                select(execution_queue)
                .where(*conditions)
                .order_by(
                    execution_queue.c.priority,
                    execution_queue.c.created_at,
                )
                .limit(1)
            )
            if connection.dialect.name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            row = connection.execute(statement).mappings().one_or_none()
            if row is None:
                return None
            claimed = connection.execute(
                update(execution_queue)
                .where(
                    execution_queue.c.execution_id == row["execution_id"],
                    execution_queue.c.status == ExecutionJobStatus.QUEUED.value,
                )
                .values(
                    status=ExecutionJobStatus.RUNNING.value,
                    attempt_count=execution_queue.c.attempt_count + 1,
                    claimed_at=current,
                    lease_owner=worker_id,
                    lease_expires_at=lease_expires,
                    heartbeat_at=current,
                    error_message=None,
                )
            )
            if claimed.rowcount != 1:
                return None
            refreshed = connection.execute(
                select(execution_queue).where(
                    execution_queue.c.execution_id == row["execution_id"]
                )
            ).mappings().one()
        return _job(refreshed)

    def heartbeat(
        self,
        execution_id: str,
        *,
        worker_id: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> bool:
        current = _utc(now)
        with self._database.begin() as connection:
            result = connection.execute(
                update(execution_queue)
                .where(
                    execution_queue.c.execution_id == execution_id,
                    execution_queue.c.status == ExecutionJobStatus.RUNNING.value,
                    execution_queue.c.lease_owner == worker_id,
                )
                .values(
                    heartbeat_at=current,
                    lease_expires_at=current
                    + timedelta(seconds=max(lease_seconds, 30)),
                )
            )
        return result.rowcount == 1

    def complete(
        self,
        execution_id: str,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> None:
        self._finish(
            execution_id,
            worker_id=worker_id,
            status=ExecutionJobStatus.SUCCESS,
            error_message=None,
            now=now,
        )

    def fail(
        self,
        execution_id: str,
        *,
        worker_id: str,
        error_message: str,
        now: datetime | None = None,
    ) -> None:
        self._finish(
            execution_id,
            worker_id=worker_id,
            status=ExecutionJobStatus.FAILED,
            error_message=error_message,
            now=now,
        )

    def cancel_claimed(
        self,
        execution_id: str,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> None:
        self._finish(
            execution_id,
            worker_id=worker_id,
            status=ExecutionJobStatus.CANCELLED,
            error_message="Stopped safely after the active Oracle step completed.",
            now=now,
        )

    def _finish(
        self,
        execution_id: str,
        *,
        worker_id: str,
        status: ExecutionJobStatus,
        error_message: str | None,
        now: datetime | None,
    ) -> None:
        current = _utc(now)
        with self._database.begin() as connection:
            result = connection.execute(
                update(execution_queue)
                .where(
                    execution_queue.c.execution_id == execution_id,
                    execution_queue.c.status == ExecutionJobStatus.RUNNING.value,
                    execution_queue.c.lease_owner == worker_id,
                )
                .values(
                    status=status.value,
                    completed_at=current,
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=current,
                    error_message=(
                        " ".join(error_message.split())[:1_500]
                        if error_message
                        else None
                    ),
                )
            )
        if result.rowcount != 1:
            raise ExecutionQueueError(
                f"Worker '{worker_id}' no longer owns execution "
                f"'{execution_id}'."
            )

    def recover_expired(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[ExecutionJob, ...]:
        """Quarantine expired leases instead of risking duplicate Oracle work."""
        current = _utc(now)
        message = (
            "The worker lease expired before completion. Oracle outcome may "
            "be unknown; review Oracle Job Console before retrying."
        )
        with self._database.begin() as connection:
            identifiers = tuple(
                str(value)
                for value in connection.execute(
                    select(execution_queue.c.execution_id).where(
                        execution_queue.c.status
                        == ExecutionJobStatus.RUNNING.value,
                        execution_queue.c.lease_expires_at.is_not(None),
                        execution_queue.c.lease_expires_at < current,
                    )
                ).scalars()
            )
            if not identifiers:
                return ()
            connection.execute(
                update(execution_queue)
                .where(execution_queue.c.execution_id.in_(identifiers))
                .values(
                    status=ExecutionJobStatus.RECOVERY_REQUIRED.value,
                    completed_at=current,
                    lease_owner=None,
                    lease_expires_at=None,
                    error_message=message,
                )
            )
            rows = connection.execute(
                select(execution_queue).where(
                    execution_queue.c.execution_id.in_(identifiers)
                )
            ).mappings().all()
        return tuple(_job(row) for row in rows)


def _job(row) -> ExecutionJob:
    return ExecutionJob(
        execution_id=str(row["execution_id"]),
        job_type=ExecutionJobType(str(row["job_type"])),
        target_key=str(row["target_key"]),
        payload=dict(row["payload"] or {}),
        status=ExecutionJobStatus(str(row["status"])),
        priority=int(row["priority"]),
        attempt_count=int(row["attempt_count"]),
        created_at=utc_datetime(row["created_at"]),
        available_at=utc_datetime(row["available_at"]),
        claimed_at=utc_datetime(row["claimed_at"]),
        lease_owner=row["lease_owner"],
        lease_expires_at=utc_datetime(row["lease_expires_at"]),
        heartbeat_at=utc_datetime(row["heartbeat_at"]),
        completed_at=utc_datetime(row["completed_at"]),
        error_message=row["error_message"],
        cancellation_requested_at=utc_datetime(
            row["cancellation_requested_at"]
        ),
        cancellation_requested_by=row["cancellation_requested_by"],
    )


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        return current.replace(tzinfo=UTC)
    return current.astimezone(UTC)
