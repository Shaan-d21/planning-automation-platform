"""Tests for standalone operation background management."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

import pytest

from app.application.operation_execution_manager import (
    OperationExecutionManager,
    OperationExecutionStatus,
)
from app.application.operations import BusinessRuleOperationInput
from app.config.settings import Settings
from app.models.workflow import WorkflowRun, WorkflowStatus
from app.utils.exceptions import OperationError


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="administrator",
        epm_password="secret",
        application_name="Vision",
        workflow_database_file=tmp_path / "history.sqlite3",
    )


@patch(
    "app.application.operation_execution_manager.OperationCommandExecutor"
)
def test_manager_rejects_duplicate_active_rule(
    executor_class: Mock,
    tmp_path: Path,
) -> None:
    started = Event()
    release = Event()

    def execute(operation_input, *, execution_id, log_file):
        started.set()
        assert release.wait(timeout=2)
        return WorkflowRun(
            execution_id=execution_id,
            workflow_name=f"Business Rule · {operation_input.rule_name}",
            status=WorkflowStatus.SUCCESS,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )

    executor_class.return_value.execute.side_effect = execute
    manager = OperationExecutionManager(_settings(tmp_path))
    operation_input = BusinessRuleOperationInput(
        rule_name="Calculate Revenue",
        runtime_prompts={},
    )

    execution = manager.submit(operation_input)
    assert started.wait(timeout=2)

    with pytest.raises(OperationError, match="active execution"):
        manager.submit(operation_input)

    release.set()
    manager._futures[execution.execution_id].result(timeout=2)

    assert manager.get(execution.execution_id).status is (
        OperationExecutionStatus.SUCCESS
    )
    manager.shutdown()


@patch(
    "app.application.operation_execution_manager.OperationCommandExecutor"
)
def test_governance_callback_runs_before_operation_is_queued(
    executor_class: Mock,
    tmp_path: Path,
) -> None:
    manager = OperationExecutionManager(_settings(tmp_path))
    operation_input = BusinessRuleOperationInput(
        rule_name="Calculate Revenue",
        runtime_prompts={},
    )

    with pytest.raises(RuntimeError, match="correlation failed"):
        manager.submit(
            operation_input,
            on_queued=lambda _: (_ for _ in ()).throw(
                RuntimeError("correlation failed")
            ),
        )

    executor_class.assert_not_called()
    assert manager._executions == {}
    manager.shutdown()


@patch(
    "app.application.operation_execution_manager.OperationCommandExecutor"
)
def test_early_operation_failure_is_retained_durably(
    executor_class: Mock,
    tmp_path: Path,
) -> None:
    executor_class.return_value.execute.side_effect = RuntimeError(
        "Oracle setup failed"
    )
    manager = OperationExecutionManager(_settings(tmp_path))
    execution = manager.submit(
        BusinessRuleOperationInput(
            rule_name="Calculate Revenue",
            runtime_prompts={},
        )
    )

    with pytest.raises(RuntimeError, match="Oracle setup failed"):
        manager._futures[execution.execution_id].result(timeout=2)

    retained = manager.get_workflow(execution.execution_id)
    assert retained is not None
    assert retained.status is WorkflowStatus.FAILED
    assert retained.error_message == "Oracle setup failed"
    manager.shutdown()
