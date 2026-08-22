"""Tests for reusable Oracle Planning job polling."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.models.job import JobDiagnostics, JobResult
from app.monitoring.job_monitor import JobMonitor
from app.services.job_service import JobService
from app.utils.exceptions import JobFailedError, JobTimeoutError


class FakeTime:
    """Deterministic clock and sleeper for polling tests."""

    def __init__(self) -> None:
        self.current = 0.0

    def clock(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.current += seconds


def test_monitor_returns_successful_terminal_job() -> None:
    service = Mock(spec=JobService)
    service.get_job_status.side_effect = [
        JobResult(job_id=100, status=-1, descriptive_status="Processing"),
        JobResult(job_id=100, status=0, descriptive_status="Completed"),
    ]
    fake_time = FakeTime()
    monitor = JobMonitor(
        service,
        poll_interval=5,
        timeout=60,
        clock=fake_time.clock,
        sleeper=fake_time.sleep,
    )

    result = monitor.wait_for_completion(100)

    assert result.is_successful
    assert service.get_job_status.call_count == 2
    service.get_failure_diagnostics.assert_not_called()


def test_monitor_collects_diagnostics_and_raises_on_failure() -> None:
    failed_job = JobResult(
        job_id=101,
        status=1,
        descriptive_status="Error",
        details="Metadata import failed",
    )
    diagnostics = JobDiagnostics(
        job=failed_job,
        details={"items": [{"recordsRejected": 1}]},
    )
    service = Mock(spec=JobService)
    service.get_job_status.return_value = failed_job
    service.get_failure_diagnostics.return_value = diagnostics
    fake_time = FakeTime()
    monitor = JobMonitor(
        service,
        poll_interval=5,
        timeout=60,
        clock=fake_time.clock,
        sleeper=fake_time.sleep,
    )

    with pytest.raises(JobFailedError) as error:
        monitor.wait_for_completion(101)

    assert error.value.job is failed_job
    assert error.value.diagnostics is diagnostics


def test_monitor_raises_when_timeout_expires() -> None:
    service = Mock(spec=JobService)
    service.get_job_status.return_value = JobResult(
        job_id=102,
        status=-1,
        descriptive_status="Processing",
    )
    fake_time = FakeTime()
    monitor = JobMonitor(
        service,
        poll_interval=5,
        timeout=10,
        clock=fake_time.clock,
        sleeper=fake_time.sleep,
    )

    with pytest.raises(JobTimeoutError, match="10 seconds"):
        monitor.wait_for_completion(102)

    assert service.get_job_status.call_count == 2
