"""Durable worker runtime for Oracle EPM operations and processes."""

from __future__ import annotations

import logging
import os
import shutil
import socket
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4
from collections.abc import Callable

from app.application.execution_payloads import (
    operation_from_payload,
    process_from_payload,
    standalone_flow_from_payload,
    upload_paths,
)
from app.application.operations import OperationCommandExecutor
from app.application.planning_process import PlanningProcessCommandExecutor
from app.application.standalone_flow import StandaloneFlowCommandExecutor
from app.config.settings import Settings
from app.models.execution_queue import ExecutionJob, ExecutionJobType
from app.models.workflow import WorkflowRun, WorkflowStatus
from app.services.execution_queue_repository import (
    SQLExecutionQueueRepository,
)
from app.services.workflow_repository import SQLWorkflowRepository


class DurableExecutionWorker:
    """Claim leased jobs, execute them once, and persist terminal evidence."""

    def __init__(
        self,
        settings: Settings,
        *,
        worker_id: str | None = None,
        operation_executor_factory: Callable[..., object] | None = None,
        process_executor_factory: Callable[..., object] | None = None,
        standalone_flow_executor_factory: Callable[..., object] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._worker_id = worker_id or (
            f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
        )
        self._logger = logger or logging.getLogger(__name__)
        self._operation_executor_factory = (
            operation_executor_factory or OperationCommandExecutor
        )
        self._process_executor_factory = (
            process_executor_factory or PlanningProcessCommandExecutor
        )
        self._standalone_flow_executor_factory = (
            standalone_flow_executor_factory or StandaloneFlowCommandExecutor
        )
        self._queue = SQLExecutionQueueRepository(settings.database_target)
        self._workflows = SQLWorkflowRepository(settings.database_target)
        self._stop = Event()

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def run_once(
        self,
        execution_id: str | None = None,
        *,
        raise_failures: bool = False,
    ) -> bool:
        """Execute one available job and return whether work was claimed."""
        job = (
            self._queue.claim(
                execution_id,
                worker_id=self._worker_id,
                lease_seconds=self._settings.execution_lease_seconds,
            )
            if execution_id
            else self._queue.claim_next(
                worker_id=self._worker_id,
                lease_seconds=self._settings.execution_lease_seconds,
            )
        )
        if job is None:
            return False
        self._execute(job, raise_failures=raise_failures)
        return True

    def run_forever(self) -> None:
        """Poll until shutdown; intended for the separate worker process."""
        self.recover_expired()
        self._logger.info(
            "Durable execution worker started: worker_id='%s'.",
            self._worker_id,
        )
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:
                self._logger.exception("Durable worker polling cycle failed.")
                worked = False
            if not worked:
                self._stop.wait(
                    self._settings.execution_worker_poll_interval
                )
        self._logger.info(
            "Durable execution worker stopped: worker_id='%s'.",
            self._worker_id,
        )

    def shutdown(self) -> None:
        self._stop.set()

    def recover_expired(self) -> int:
        """Quarantine abandoned leases without repeating Oracle work."""
        jobs = self._queue.recover_expired()
        for job in jobs:
            self._record_failure(
                job,
                job.error_message
                or "Execution requires recovery after its worker lease expired.",
            )
        if jobs:
            self._logger.error(
                "%d execution(s) require manual recovery after worker loss.",
                len(jobs),
            )
        return len(jobs)

    def _execute(
        self,
        job: ExecutionJob,
        *,
        raise_failures: bool,
    ) -> None:
        heartbeat_stop = Event()
        heartbeat = Thread(
            target=self._heartbeat,
            args=(job.execution_id, heartbeat_stop),
            name=f"epm-heartbeat-{job.execution_id[:8]}",
            daemon=True,
        )
        heartbeat.start()
        try:
            if job.job_type is ExecutionJobType.OPERATION:
                operation_input, actor = operation_from_payload(job.payload)
                executor = self._operation_executor_factory(
                    self._settings,
                    logger=self._logger.getChild(job.execution_id[:8]),
                )
                arguments = {
                    "execution_id": job.execution_id,
                    "log_file": self._log_file(job),
                }
                if actor is not None:
                    arguments["actor"] = actor
                run = executor.execute(
                    operation_input,
                    **arguments,
                )
            elif job.job_type is ExecutionJobType.PROCESS:
                process_input, actor = process_from_payload(job.payload)
                executor = self._process_executor_factory(
                    self._settings,
                    logger=self._logger.getChild(job.execution_id[:8]),
                )
                arguments = {
                    "execution_id": job.execution_id,
                    "log_file": self._log_file(job),
                }
                if actor is not None:
                    arguments["actor"] = actor
                run = executor.execute(
                    process_input,
                    **arguments,
                )
            elif job.job_type is ExecutionJobType.STANDALONE_FLOW:
                flow_input, actor = standalone_flow_from_payload(job.payload)
                executor = self._standalone_flow_executor_factory(
                    self._settings,
                    logger=self._logger.getChild(job.execution_id[:8]),
                )
                arguments = {
                    "execution_id": job.execution_id,
                    "log_file": self._log_file(job),
                }
                if actor is not None:
                    arguments["actor"] = actor
                run = executor.execute(flow_input, **arguments)
            else:  # pragma: no cover - enum compatibility protection.
                raise ValueError(
                    f"Unsupported execution job type '{job.job_type.value}'."
                )
            if isinstance(run, WorkflowRun):
                self._workflows.save(run)
            self._queue.complete(
                job.execution_id,
                worker_id=self._worker_id,
            )
        except Exception as exc:
            message = " ".join(str(exc).split())[:1_500]
            self._record_failure(job, message)
            try:
                self._queue.fail(
                    job.execution_id,
                    worker_id=self._worker_id,
                    error_message=message,
                )
            except Exception:
                self._logger.exception(
                    "Unable to persist queue failure for execution '%s'.",
                    job.execution_id,
                )
            self._logger.exception(
                "Durable execution failed: execution_id='%s'.",
                job.execution_id,
            )
            if raise_failures:
                raise
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=2)
            self._cleanup_uploads(job)

    def _heartbeat(self, execution_id: str, stop: Event) -> None:
        interval = max(
            10.0,
            min(self._settings.execution_lease_seconds / 3, 30.0),
        )
        while not stop.wait(interval):
            try:
                if not self._queue.heartbeat(
                    execution_id,
                    worker_id=self._worker_id,
                    lease_seconds=self._settings.execution_lease_seconds,
                ):
                    self._logger.error(
                        "Worker lease was lost: execution_id='%s'.",
                        execution_id,
                    )
                    return
            except Exception:
                self._logger.exception(
                    "Execution heartbeat failed: execution_id='%s'.",
                    execution_id,
                )

    def _record_failure(self, job: ExecutionJob, message: str) -> None:
        existing = self._workflows.get(job.execution_id)
        now = datetime.now(UTC)
        if existing is None:
            existing = WorkflowRun(
                execution_id=job.execution_id,
                workflow_name=job.target_key,
                status=WorkflowStatus.FAILED,
                started_at=job.created_at,
                oracle_execution_username=(
                    self._settings.oracle_execution_username
                ),
            )
        if existing.status is WorkflowStatus.SUCCESS:
            return
        self._workflows.save(
            replace(
                existing,
                status=WorkflowStatus.FAILED,
                completed_at=now,
                error_message=message,
                oracle_execution_username=(
                    existing.oracle_execution_username
                    or self._settings.oracle_execution_username
                ),
            )
        )

    def _log_file(self, job: ExecutionJob) -> Path:
        folder = (
            "operation_logs"
            if job.job_type is ExecutionJobType.OPERATION
            else "process_logs"
        )
        return (
            self._settings.runtime_storage_dir
            / folder
            / f"{job.execution_id}.log"
        )

    def _cleanup_uploads(self, job: ExecutionJob) -> None:
        allowed_root = (
            self._settings.runtime_storage_dir / "web_uploads"
        ).resolve()
        for path in upload_paths(job.payload):
            try:
                resolved = path.resolve()
                if not resolved.is_relative_to(allowed_root):
                    self._logger.error(
                        "Refused upload cleanup outside runtime storage: %s",
                        resolved,
                    )
                    continue
                directory = resolved.parent
                if directory == allowed_root:
                    resolved.unlink(missing_ok=True)
                else:
                    shutil.rmtree(directory, ignore_errors=False)
            except FileNotFoundError:
                continue
            except Exception:
                self._logger.exception(
                    "Unable to clean durable upload '%s'.",
                    path,
                )
