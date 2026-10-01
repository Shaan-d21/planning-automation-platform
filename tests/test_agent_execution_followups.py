"""Tests for durable, evidence-backed agent execution follow-ups."""

from datetime import UTC, datetime
from pathlib import Path

from app.agent.repository import SQLiteAgentRepository
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
from app.services.access_control_service import AccessControlService
from app.services.agent_execution_followup_service import (
    AgentExecutionFollowUpService,
)
from app.services.execution_queue_repository import SQLExecutionQueueRepository
from app.services.workflow_repository import SQLWorkflowRepository


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="service-user",
        epm_password="secret",
        application_name="Vision",
        workflow_database_file=tmp_path / "followups.sqlite3",
    )


def _approved_execution(
    settings: Settings,
    *,
    execution_id: str,
    operation_code: str,
    artifact_name: str,
):
    access = AccessControlService(settings.database_target)
    account = access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.database_target)
    conversation = repository.create_conversation(
        user_id=account.user_id,
        provider="groq",
        model="test-model",
    )
    repository.reserve_action_decision(
        request_id=f"request-{execution_id}",
        conversation_id=conversation.conversation_id,
        user_id=account.user_id,
        username=account.username,
        operation_code=operation_code,
        artifact_name=artifact_name,
        decision="APPROVE",
        payload_checksum="a" * 64,
        payload_snapshot={"artifact_name": artifact_name},
    )
    repository.finalize_action_decision(
        request_id=f"request-{execution_id}",
        user_id=account.user_id,
        conversation_id=conversation.conversation_id,
        outcome_status="SUBMITTED",
        execution_id=execution_id,
    )
    return repository, conversation, account


def test_success_followup_is_evidence_backed_and_idempotent(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    repository, conversation, account = _approved_execution(
        settings,
        execution_id="load-actuals",
        operation_code="data-import",
        artifact_name="Import Actuals",
    )
    queue = SQLExecutionQueueRepository(settings.database_target)
    queue.enqueue(
        ExecutionJobSubmission(
            execution_id="load-actuals",
            job_type=ExecutionJobType.OPERATION,
            target_key="DATA_IMPORT:Import Actuals",
        )
    )
    queue.claim("load-actuals", worker_id="worker-1", lease_seconds=60)
    started = datetime.now(UTC)
    SQLWorkflowRepository(settings.database_target).save(
        WorkflowRun(
            execution_id="load-actuals",
            workflow_name="Planning Data Import - Import Actuals",
            status=WorkflowStatus.SUCCESS,
            started_at=started,
            completed_at=started,
            steps=(
                WorkflowStepResult(
                    name="Import data",
                    sequence=1,
                    status=WorkflowStepStatus.SUCCESS,
                    details={
                        "record_statistics": {
                            "records_read": 125,
                            "records_processed": 124,
                            "records_rejected": 1,
                            "details": [],
                        }
                    },
                ),
            ),
        )
    )
    queue.complete("load-actuals", worker_id="worker-1")
    service = AgentExecutionFollowUpService(
        settings.database_target,
        repository=repository,
    )

    first = service.publish("load-actuals")
    second = service.publish("load-actuals")

    assert first is not None
    assert second is None
    assert "Import Actuals completed successfully" in first.content
    assert "125 read" in first.content
    assert "124 processed" in first.content
    assert "1 rejected" in first.content
    messages = repository.list_messages(
        conversation.conversation_id,
        account.user_id,
    )
    assert messages == (first,)


def test_reopening_conversation_reconciles_a_missed_failed_followup(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    repository, conversation, account = _approved_execution(
        settings,
        execution_id="failed-rule",
        operation_code="business-rules",
        artifact_name="Calculate Forecast",
    )
    queue = SQLExecutionQueueRepository(settings.database_target)
    queue.enqueue(
        ExecutionJobSubmission(
            execution_id="failed-rule",
            job_type=ExecutionJobType.OPERATION,
            target_key="BUSINESS_RULE:Calculate Forecast",
        )
    )
    queue.claim("failed-rule", worker_id="worker-1", lease_seconds=60)
    started = datetime.now(UTC)
    SQLWorkflowRepository(settings.database_target).save(
        WorkflowRun(
            execution_id="failed-rule",
            workflow_name="Business Rule - Calculate Forecast",
            status=WorkflowStatus.FAILED,
            started_at=started,
            completed_at=started,
            error_message="Oracle rejected the Year runtime prompt.",
            steps=(
                WorkflowStepResult(
                    name="Run Oracle job",
                    sequence=1,
                    status=WorkflowStepStatus.FAILED,
                    error_message="Oracle rejected the Year runtime prompt.",
                ),
            ),
        )
    )
    queue.fail(
        "failed-rule",
        worker_id="worker-1",
        error_message="Oracle rejected the Year runtime prompt.",
    )
    service = AgentExecutionFollowUpService(
        settings.database_target,
        repository=repository,
    )

    assert service.publish_for_conversation(
        conversation.conversation_id,
        account.user_id,
    ) == 1
    assert service.publish_for_conversation(
        conversation.conversation_id,
        account.user_id,
    ) == 0
    message = repository.list_messages(
        conversation.conversation_id,
        account.user_id,
    )[0]
    assert "Calculate Forecast failed" in message.content
    assert "Oracle rejected the Year runtime prompt" in message.content
