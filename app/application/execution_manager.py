"""In-process background execution manager for Planning workflows."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from app.application.planning_process import (
    PlanningProcessCommandExecutor,
    PlanningProcessInput,
)
from app.application.execution_payloads import process_payload
from app.application.execution_worker import DurableExecutionWorker
from app.config.settings import Settings
from app.models.execution_queue import (
    ExecutionJobSubmission,
    ExecutionJobType,
)
from app.models.workflow import WorkflowRun
from app.models.workflow import WorkflowStatus
from app.models.access_control import ExecutionActor, TriggerSource
from app.services.execution_queue_repository import SQLExecutionQueueRepository
from app.services.workflow_repository import SQLWorkflowRepository
from app.utils.exceptions import ExecutionQueueConflictError
from app.utils.exceptions import PlanningProcessError


class ManagedExecutionStatus(StrEnum):
    """Web-visible state before and around durable workflow execution."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


@dataclass(frozen=True, slots=True)
class ManagedExecution:
    """Thread-safe background execution metadata."""

    execution_id: str
    process_code: str
    status: ManagedExecutionStatus
    submitted_at: datetime
    log_file: Path
    error_message: str | None = None


class PlanningProcessExecutionManager:
    """Persist process work before embedded or external execution."""

    def __init__(
        self,
        settings: Settings,
        *,
        max_workers: int = 2,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._logger = logger or logging.getLogger(__name__)
        self._repository = SQLWorkflowRepository(
            settings.database_target
        )
        self._queue = SQLExecutionQueueRepository(settings.database_target)
        self._embedded = settings.execution_runtime == "embedded"
        self._worker = DurableExecutionWorker(
            settings,
            worker_id=f"embedded-process-{uuid4().hex[:12]}",
            process_executor_factory=PlanningProcessCommandExecutor,
            logger=self._logger.getChild("durable_worker"),
        )
        self._executor = (
            ThreadPoolExecutor(
                max_workers=max_workers,
                thread_name_prefix="epm-process",
            )
            if self._embedded
            else None
        )
        self._futures: dict[str, Future[WorkflowRun]] = {}

    def submit(
        self,
        process_input: PlanningProcessInput,
        *,
        cleanup: Callable[[], None] | None = None,
        actor: ExecutionActor | None = None,
    ) -> ManagedExecution:
        """Queue one run and reject another active run of the same process."""
        execution_id = str(uuid4())
        submitted_at = datetime.now(UTC)
        try:
            job = self._queue.enqueue(
                ExecutionJobSubmission(
                    execution_id=execution_id,
                    job_type=ExecutionJobType.PROCESS,
                    target_key=process_input.process_code,
                    payload=process_payload(process_input, actor),
                ),
                now=submitted_at,
            )
        except ExecutionQueueConflictError as exc:
            raise PlanningProcessError(
                f"Process '{process_input.process_code}' already has an "
                "active execution. " + str(exc)
            ) from exc
        try:
            self._repository.save(
                WorkflowRun(
                    execution_id=execution_id,
                    workflow_name=f"Planning Process - {process_input.process_code}",
                    status=WorkflowStatus.QUEUED,
                    started_at=submitted_at,
                    initiated_by=actor.username if actor else None,
                    initiated_by_display=actor.display_name if actor else None,
                    trigger_source=(
                        actor.trigger_source if actor else TriggerSource.API
                    ),
                )
            )
        except Exception:
            self._queue.discard_queued(execution_id)
            raise
        execution = self._managed(job)
        if self._executor is not None:
            future = self._executor.submit(
                self._run_embedded,
                execution_id,
                cleanup,
            )
            self._futures[execution_id] = future
        return execution

    def get(self, execution_id: str) -> ManagedExecution | None:
        """Return in-memory execution metadata."""
        job = self._queue.get(execution_id)
        if job is None or job.job_type is not ExecutionJobType.PROCESS:
            return None
        return self._managed(job)

    def get_workflow(self, execution_id: str) -> WorkflowRun | None:
        """Return the durable workflow record when orchestration has begun."""
        return self._repository.get(execution_id)

    def shutdown(self) -> None:
        """Stop accepting embedded work without cancelling Oracle jobs."""
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=False)

    def _run_embedded(
        self,
        execution_id: str,
        cleanup: Callable[[], None] | None,
    ) -> WorkflowRun | None:
        try:
            self._worker.run_once(execution_id, raise_failures=True)
            return self._repository.get(execution_id)
        finally:
            if cleanup is not None:
                try:
                    cleanup()
                except Exception:
                    self._logger.exception(
                        "Unable to clean temporary files for execution '%s'.",
                        execution_id,
                    )

    def _managed(
        self,
        job,
    ) -> ManagedExecution:
        return ManagedExecution(
            execution_id=job.execution_id,
            process_code=job.target_key,
            status=ManagedExecutionStatus(job.status.value),
            submitted_at=job.created_at,
            log_file=(
                self._settings.runtime_storage_dir
                / "process_logs"
                / f"{job.execution_id}.log"
            ),
            error_message=job.error_message,
        )
