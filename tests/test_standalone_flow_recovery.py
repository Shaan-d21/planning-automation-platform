"""Tests for reviewed retry-from-failed-step recovery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.application.execution_payloads import standalone_flow_payload
from app.application.operations import (
    BusinessRuleOperationInput,
    DataImportOperationInput,
    DataMapOperationInput,
)
from app.application.standalone_flow import (
    StandaloneFlowInput,
    StandaloneFlowStepInput,
)
from app.application.standalone_flow_recovery import (
    StandaloneFlowRecoveryService,
)
from app.config.settings import Settings
from app.models.execution_queue import (
    ExecutionJobSubmission,
    ExecutionJobType,
)
from app.models.workflow import (
    WorkflowRun,
    WorkflowStatus,
    WorkflowStepResult,
    WorkflowStepStatus,
)
from app.services.execution_queue_repository import SQLExecutionQueueRepository
from app.services.workflow_repository import SQLWorkflowRepository


class _Catalog:
    def __init__(self, *, include_data_job: bool = True) -> None:
        self.include_data_job = include_data_job

    def discover_job_names_for_types(self, job_types):
        values = {
            "RULES": ("Calculate Forecast",),
            "PLAN_TYPE_MAP": ("Forecast to Reporting",),
            "IMPORT_DATA": (
                ("Import Forecast",) if self.include_data_job else ()
            ),
        }
        return {key: values.get(key, ()) for key in job_types}

    def require_data_integration(self, name):  # pragma: no cover - fixture API
        return name

    def preflight_pipeline(self, code):  # pragma: no cover - fixture API
        return code


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="administrator",
        epm_password="secret",
        application_name="Vision",
        workflow_database_file=tmp_path / "recovery.sqlite3",
        execution_runtime="web",
    )


def _flow(source_file: Path) -> StandaloneFlowInput:
    return StandaloneFlowInput(
        name="Monthly Forecast",
        objective="Load, calculate, and publish the forecast.",
        steps=(
            StandaloneFlowStepInput(
                operation_code="business-rules",
                display_name="Business Rules",
                artifact_name="Calculate Forecast",
                operation_input=BusinessRuleOperationInput(
                    rule_name="Calculate Forecast",
                    runtime_prompts={},
                ),
            ),
            StandaloneFlowStepInput(
                operation_code="data-import",
                display_name="Planning Data Import",
                artifact_name="Import Forecast",
                operation_input=DataImportOperationInput(
                    job_name="Import Forecast",
                    upload_path=source_file,
                    inbox_file=None,
                    use_configured_file=False,
                    error_file_name="Forecast_Errors.zip",
                ),
            ),
            StandaloneFlowStepInput(
                operation_code="data-maps",
                display_name="Data Maps",
                artifact_name="Forecast to Reporting",
                operation_input=DataMapOperationInput(
                    data_map_name="Forecast to Reporting",
                    clear_target=False,
                    member_overrides={},
                    exclusion_overrides={},
                ),
            ),
        ),
    )


def _failed_execution(
    settings: Settings,
    source_file: Path,
    *,
    queue_status: str = "FAILED",
) -> None:
    execution_id = "failed-flow-1"
    queue = SQLExecutionQueueRepository(settings.database_target)
    queue.enqueue(
        ExecutionJobSubmission(
            execution_id=execution_id,
            job_type=ExecutionJobType.STANDALONE_FLOW,
            target_key="STANDALONE_FLOW:Monthly Forecast",
            payload=standalone_flow_payload(_flow(source_file), None),
        )
    )
    queue.claim(
        execution_id,
        worker_id="test-worker",
        lease_seconds=60,
    )
    if queue_status == "FAILED":
        queue.fail(
            execution_id,
            worker_id="test-worker",
            error_message="Import failed",
        )
    else:
        queue.recover_expired(now=datetime.now(UTC) + timedelta(days=1))
    now = datetime.now(UTC)
    SQLWorkflowRepository(settings.database_target).save(
        WorkflowRun(
            execution_id=execution_id,
            workflow_name="Standalone Flow - Monthly Forecast",
            status=WorkflowStatus.FAILED,
            started_at=now,
            completed_at=now,
            error_message="Import failed",
            steps=(
                WorkflowStepResult(
                    name="1. Business Rules - Calculate Forecast",
                    sequence=1,
                    status=WorkflowStepStatus.SUCCESS,
                ),
                WorkflowStepResult(
                    name="2. Planning Data Import - Import Forecast",
                    sequence=2,
                    status=WorkflowStepStatus.FAILED,
                    error_message="Invalid member in row 18.",
                ),
                WorkflowStepResult(
                    name="3. Data Maps - Forecast to Reporting",
                    sequence=3,
                    status=WorkflowStepStatus.SKIPPED,
                ),
            ),
        )
    )


def test_recovery_plan_excludes_successful_steps_and_requires_new_upload(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _failed_execution(settings, tmp_path / "expired" / "Forecast.csv")
    service = StandaloneFlowRecoveryService(settings, catalog=_Catalog())

    plan = service.plan("failed-flow-1")

    assert plan.retryable is True
    assert plan.failed_step_sequence == 2
    assert [step.sequence for step in plan.steps] == [2, 3]
    assert [step.original_status for step in plan.steps] == [
        "FAILED",
        "SKIPPED",
    ]
    assert plan.required_uploads[0].key == "step_2:source_file"
    assert plan.required_uploads[0].original_filename == "Forecast.csv"


def test_recovery_builds_linked_flow_from_failed_step(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _failed_execution(settings, tmp_path / "expired" / "Forecast.csv")
    replacement = tmp_path / "replacement.csv"
    replacement.write_text("Account,Value\nSales,100\n", encoding="utf-8")
    service = StandaloneFlowRecoveryService(settings, catalog=_Catalog())

    recovered = service.prepare_retry(
        "failed-flow-1",
        expected_failed_step=2,
        replacement_uploads={"step_2:source_file": replacement},
    )

    assert recovered.recovery_source_execution_id == "failed-flow-1"
    assert recovered.recovery_from_sequence == 2
    assert [step.source_sequence for step in recovered.steps] == [2, 3]
    assert recovered.steps[0].operation_input.upload_path == replacement
    assert [step.artifact_name for step in recovered.steps] == [
        "Import Forecast",
        "Forecast to Reporting",
    ]


def test_recovery_is_blocked_when_live_artifact_is_missing(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _failed_execution(settings, tmp_path / "expired" / "Forecast.csv")
    service = StandaloneFlowRecoveryService(
        settings,
        catalog=_Catalog(include_data_job=False),
    )

    plan = service.plan("failed-flow-1")

    assert plan.retryable is False
    assert "IMPORT_DATA: Import Forecast" in str(plan.blocked_reason)


def test_uncertain_worker_outcome_is_never_automatically_retryable(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _failed_execution(
        settings,
        tmp_path / "expired" / "Forecast.csv",
        queue_status="RECOVERY_REQUIRED",
    )
    service = StandaloneFlowRecoveryService(settings, catalog=_Catalog())

    plan = service.plan("failed-flow-1")

    assert plan.retryable is False
    assert plan.steps == ()
    assert "Oracle Job Console" in str(plan.blocked_reason)
