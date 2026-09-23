"""Tests for durable PostgreSQL-compatible execution dispatch."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.application.execution_manager import PlanningProcessExecutionManager
from app.application.execution_payloads import (
    operation_from_payload,
    operation_payload,
    process_from_payload,
    process_payload,
)
from app.application.execution_worker import DurableExecutionWorker
from app.application.operations import BusinessRuleOperationInput
from app.application.planning_process import PlanningProcessInput
from app.config.settings import Settings
from app.models.access_control import ExecutionActor, TriggerSource
from app.models.execution_queue import (
    ExecutionJobStatus,
    ExecutionJobSubmission,
    ExecutionJobType,
)
from app.models.workflow import WorkflowRun, WorkflowStatus
from app.services.execution_queue_repository import SQLExecutionQueueRepository
from app.services.workflow_repository import SQLWorkflowRepository
from app.utils.exceptions import ExecutionQueueConflictError


def _settings(tmp_path: Path, *, runtime: str = "embedded") -> Settings:
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="administrator",
        epm_password="secret",
        application_name="Vision",
        workflow_database_file=tmp_path / "durable.sqlite3",
        execution_runtime=runtime,
        execution_lease_seconds=60,
    )


def test_queue_claim_heartbeat_and_completion_are_durable(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    repository = SQLExecutionQueueRepository(settings.database_target)
    created = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    repository.enqueue(
        ExecutionJobSubmission(
            execution_id="run-1",
            job_type=ExecutionJobType.PROCESS,
            target_key="MONTHLY_FORECAST",
            payload={"version": 1, "input": {}, "actor": None},
        ),
        now=created,
    )

    claimed = repository.claim_next(
        worker_id="worker-a",
        lease_seconds=60,
        now=created,
    )

    assert claimed is not None
    assert claimed.status is ExecutionJobStatus.RUNNING
    assert claimed.attempt_count == 1
    assert repository.heartbeat(
        "run-1",
        worker_id="worker-a",
        lease_seconds=60,
        now=created + timedelta(seconds=20),
    )
    repository.complete(
        "run-1",
        worker_id="worker-a",
        now=created + timedelta(seconds=30),
    )
    assert repository.get("run-1").status is ExecutionJobStatus.SUCCESS


def test_queue_prevents_duplicate_active_target(tmp_path: Path) -> None:
    repository = SQLExecutionQueueRepository(_settings(tmp_path).database_target)
    first = ExecutionJobSubmission(
        execution_id="run-1",
        job_type=ExecutionJobType.OPERATION,
        target_key="BUSINESS_RULE:Calculate Revenue",
        payload={"version": 1},
    )
    repository.enqueue(first)

    with pytest.raises(ExecutionQueueConflictError, match="run-1"):
        repository.enqueue(replace(first, execution_id="run-2"))


def test_queue_cancels_unclaimed_work_and_requests_safe_stop_for_running_work(
    tmp_path: Path,
) -> None:
    repository = SQLExecutionQueueRepository(_settings(tmp_path).database_target)
    queued = ExecutionJobSubmission(
        execution_id="queued-flow",
        job_type=ExecutionJobType.STANDALONE_FLOW,
        target_key="STANDALONE_FLOW:Month Close",
        payload={"version": 1},
    )
    running = replace(
        queued,
        execution_id="running-flow",
        target_key="STANDALONE_FLOW:Forecast Seed",
    )
    repository.enqueue(queued)
    repository.enqueue(running)
    repository.claim(
        "running-flow",
        worker_id="worker-a",
        lease_seconds=60,
    )

    cancelled = repository.request_cancellation(
        "queued-flow",
        requested_by="planner",
    )
    stop_requested = repository.request_cancellation(
        "running-flow",
        requested_by="planner",
    )

    assert cancelled.status is ExecutionJobStatus.CANCELLED
    assert cancelled.status.terminal is True
    assert stop_requested.status is ExecutionJobStatus.RUNNING
    assert stop_requested.cancellation_requested_at is not None
    assert stop_requested.cancellation_requested_by == "planner"
    assert repository.cancellation_requested("running-flow") is True
    repository.cancel_claimed("running-flow", worker_id="worker-a")
    assert repository.get("running-flow").status is ExecutionJobStatus.CANCELLED


def test_expired_lease_requires_review_instead_of_automatic_retry(
    tmp_path: Path,
) -> None:
    repository = SQLExecutionQueueRepository(_settings(tmp_path).database_target)
    created = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    repository.enqueue(
        ExecutionJobSubmission(
            execution_id="uncertain-run",
            job_type=ExecutionJobType.OPERATION,
            target_key="DATA_MAP:Publish Reporting",
            payload={"version": 1},
        ),
        now=created,
    )
    repository.claim(
        "uncertain-run",
        worker_id="lost-worker",
        lease_seconds=30,
        now=created,
    )

    recovered = repository.recover_expired(
        now=created + timedelta(seconds=31)
    )

    assert recovered[0].status is ExecutionJobStatus.RECOVERY_REQUIRED
    assert repository.claim_next(
        worker_id="replacement",
        lease_seconds=30,
        now=created + timedelta(seconds=32),
    ) is None


def test_operation_and_process_payloads_round_trip() -> None:
    actor = ExecutionActor(
        username="planner",
        display_name="Planning User",
        trigger_source=TriggerSource.MANUAL,
    )
    operation = BusinessRuleOperationInput(
        rule_name="Calculate Revenue",
        runtime_prompts={"Year": "FY27"},
    )
    process = PlanningProcessInput(
        process_code="MONTHLY_FORECAST",
        year="FY27",
        start_period="Jan",
        end_period="Mar",
        pipeline_variables={"IMPORTMODE": "Replace"},
    )

    decoded_operation, operation_actor = operation_from_payload(
        operation_payload(operation, actor)
    )
    decoded_process, process_actor = process_from_payload(
        process_payload(process, actor)
    )

    assert decoded_operation == operation
    assert operation_actor == actor
    assert decoded_process == process
    assert process_actor == actor


def test_web_runtime_queues_without_executing_and_worker_completes(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, runtime="web")
    manager = PlanningProcessExecutionManager(settings)
    submitted = manager.submit(
        PlanningProcessInput(
            process_code="MONTHLY_FORECAST",
            year="FY27",
            start_period="Jan",
            end_period="Mar",
        )
    )
    assert manager.get(submitted.execution_id).status.value == "QUEUED"

    class Executor:
        def execute(self, process_input, *, execution_id, log_file):
            return WorkflowRun(
                execution_id=execution_id,
                workflow_name=process_input.process_code,
                status=WorkflowStatus.SUCCESS,
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )

    notified: list[str] = []
    worker = DurableExecutionWorker(
        settings,
        worker_id="worker-1",
        process_executor_factory=lambda *_args, **_kwargs: Executor(),
        completion_notifier=notified.append,
    )
    assert worker.run_once()
    assert manager.get(submitted.execution_id).status.value == "SUCCESS"
    assert (
        SQLWorkflowRepository(settings.database_target)
        .get(submitted.execution_id)
        .status
        is WorkflowStatus.SUCCESS
    )
    assert notified == [submitted.execution_id]
