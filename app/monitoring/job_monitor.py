"""Polling monitor for asynchronous Oracle Planning jobs."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Protocol

from app.models.job import JobDiagnostics, JobResult
from app.utils.exceptions import (
    EPMError,
    JobFailedError,
    JobTimeoutError,
)


class JobStatusService(Protocol):
    """Operations required by the reusable asynchronous job monitor."""

    def get_job_status(self, job_id: int) -> JobResult:
        """Retrieve the current normalized status for a job."""

    def get_failure_diagnostics(
        self,
        job: JobResult,
    ) -> JobDiagnostics:
        """Retrieve available diagnostics for a failed job."""


class JobMonitor:
    """Poll Oracle Planning jobs until they complete, fail, or time out."""

    def __init__(
        self,
        job_service: JobStatusService,
        *,
        poll_interval: float,
        timeout: float,
        logger: logging.Logger | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        """Initialize configurable polling and dependency-injected timing."""
        if poll_interval <= 0:
            raise ValueError("poll_interval must be greater than zero.")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero.")

        self._job_service = job_service
        self._poll_interval = poll_interval
        self._timeout = timeout
        self._logger = logger or logging.getLogger(__name__)
        self._clock = clock
        self._sleeper = sleeper

    def wait_for_completion(self, job_id: int) -> JobResult:
        """Wait for a job and return success or raise a typed terminal error."""
        started_at = self._clock()
        self._logger.info(
            "Polling job status started: job_id=%s, interval=%ss, timeout=%ss.",
            job_id,
            self._poll_interval,
            self._timeout,
        )

        while True:
            elapsed = self._clock() - started_at
            if elapsed >= self._timeout:
                self._logger.error(
                    "Job monitoring timed out: job_id=%s, elapsed=%.2fs.",
                    job_id,
                    elapsed,
                )
                raise JobTimeoutError(job_id, self._timeout)

            job = self._job_service.get_job_status(job_id)
            self._logger.info(
                "Current job status: job_id=%s, status=%s, "
                "descriptive_status='%s'.",
                job.job_id,
                job.status,
                job.descriptive_status or "Unknown",
            )

            if job.is_successful:
                self._logger.info(
                    "Job completed successfully: job_id=%s, "
                    "execution_time=%.2fs.",
                    job.job_id,
                    self._clock() - started_at,
                )
                return job

            if job.is_failed:
                diagnostics = self._collect_diagnostics(job)
                self._logger.error(
                    "Job failed: job_id=%s, status=%s, details='%s', "
                    "execution_time=%.2fs.",
                    job.job_id,
                    job.status,
                    job.details or job.descriptive_status or "Unavailable",
                    self._clock() - started_at,
                )
                raise JobFailedError(job, diagnostics=diagnostics)

            self._sleeper(self._poll_interval)

    def _collect_diagnostics(
        self,
        job: JobResult,
    ) -> JobDiagnostics | None:
        try:
            return self._job_service.get_failure_diagnostics(job)
        except EPMError as exc:
            self._logger.warning(
                "Job %s failed and additional diagnostics were unavailable: %s",
                job.job_id,
                exc,
            )
            return None
