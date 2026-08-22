"""Tests for background Planning process execution management."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

import pytest

from app.application.execution_manager import (
    ManagedExecutionStatus,
    PlanningProcessExecutionManager,
)
from app.application.planning_process import PlanningProcessInput
from app.config.settings import Settings
from app.models.workflow import WorkflowRun, WorkflowStatus
from app.utils.exceptions import PlanningProcessError


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="administrator",
        epm_password="secret",
        application_name="Vision",
        workflow_database_file=tmp_path / "history.sqlite3",
    )


@patch("app.application.execution_manager.PlanningProcessCommandExecutor")
def test_manager_rejects_duplicate_active_process_and_cleans_uploads(
    executor_class: Mock,
    tmp_path: Path,
) -> None:
    started = Event()
    release = Event()
    cleanup = Mock()

    def execute(process_input, *, execution_id, log_file):
        started.set()
        assert release.wait(timeout=2)
        return WorkflowRun(
            execution_id=execution_id,
            workflow_name=process_input.process_code,
            status=WorkflowStatus.SUCCESS,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )

    executor_class.return_value.execute.side_effect = execute
    manager = PlanningProcessExecutionManager(_settings(tmp_path))
    process_input = PlanningProcessInput(
        process_code="MONTHLY_FORECAST_PROCESS",
        year="FY26",
        start_period="Jan",
        end_period="Mar",
    )

    execution = manager.submit(process_input, cleanup=cleanup)
    assert started.wait(timeout=2)

    with pytest.raises(PlanningProcessError, match="active execution"):
        manager.submit(process_input)

    release.set()
    manager._futures[execution.execution_id].result(timeout=2)

    assert manager.get(execution.execution_id).status is (
        ManagedExecutionStatus.SUCCESS
    )
    cleanup.assert_called_once_with()
    call = executor_class.return_value.execute.call_args
    assert call.kwargs["execution_id"] == execution.execution_id
    assert call.kwargs["log_file"].name == f"{execution.execution_id}.log"
    manager.shutdown()
