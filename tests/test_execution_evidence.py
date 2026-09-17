"""Tests for user-facing Oracle execution evidence projections."""

from datetime import UTC, datetime, timedelta

from app.application.execution_evidence import (
    agent_execution_evidence,
    aggregate_import_evidence,
    aggregate_record_statistics,
    workflow_failure_details,
)
from app.models.access_control import TriggerSource
from app.models.job import JobDiagnostics, JobResult
from app.models.workflow import (
    WorkflowRun,
    WorkflowStatus,
    WorkflowStepResult,
    WorkflowStepStatus,
)
from app.utils.exceptions import JobFailedError


def test_record_statistics_are_combined_across_compatible_steps() -> None:
    statistics = aggregate_record_statistics(
        (
            {
                "record_statistics": {
                    "records_read": 10,
                    "records_processed": 9,
                    "records_rejected": 1,
                    "details": [{"dimension_name": "Account"}],
                }
            },
            {"status": "Completed"},
            {
                "record_statistics": {
                    "records_read": 5,
                    "records_processed": 5,
                    "records_rejected": 0,
                    "details": [{"dimension_name": "Entity"}],
                }
            },
        )
    )

    assert statistics == {
        "source": "ORACLE_JOB_DETAILS",
        "records_read": 15,
        "records_processed": 14,
        "records_rejected": 1,
        "details": [
            {"dimension_name": "Account"},
            {"dimension_name": "Entity"},
        ],
    }


def test_record_statistics_are_absent_for_unsupported_operations() -> None:
    assert aggregate_record_statistics(({"job_id": 42},)) is None


def test_record_statistics_preserve_data_integration_log_source() -> None:
    statistics = aggregate_record_statistics(({
        "record_statistics": {
            "source": "ORACLE_DATA_INTEGRATION_LOG",
            "records_read": 20,
            "records_processed": 19,
            "records_rejected": 1,
            "details": [],
        }
    },))

    assert statistics is not None
    assert statistics["source"] == "ORACLE_DATA_INTEGRATION_LOG"


def test_import_evidence_projects_lineage_messages_and_artifacts() -> None:
    evidence = aggregate_import_evidence(({
        "load_lineage": {
            "source_kind": "existing_inbox",
            "source_file": "ERP_Actuals.csv",
            "target_application": "Vision",
            "password": "must-not-leak",
        },
        "oracle_messages": [{
            "message_type": "WARN",
            "category": "Data Import",
            "message": "One record was rejected.",
            "dimension_name": "Account",
        }],
        "artifacts": [{
            "artifact_id": "abc123",
            "stored_name": "private-name.zip",
            "name": "DataErrors.zip",
            "kind": "ORACLE_ERROR_FILE",
            "size_bytes": 128,
        }],
    },))

    assert evidence["lineage"]["source_file"] == "ERP_Actuals.csv"
    assert "password" not in evidence["lineage"]
    assert evidence["oracle_messages"][0]["message_type"] == "WARN"
    assert evidence["artifacts"] == [{
        "artifact_id": "abc123",
        "name": "DataErrors.zip",
        "kind": "ORACLE_ERROR_FILE",
        "size_bytes": 128,
    }]


def test_failed_load_retains_oracle_record_statistics() -> None:
    job = JobResult(
        job_id=42,
        status=1,
        descriptive_status="Error",
    )
    error = JobFailedError(
        job,
        diagnostics=JobDiagnostics(
            job=job,
            details={
                "items": [
                    {
                        "dimensionName": "Entity",
                        "recordsRead": 10,
                        "recordsProcessed": 8,
                        "recordsRejected": 2,
                    }
                ]
            },
        ),
    )

    evidence = workflow_failure_details(error)

    assert evidence["job_id"] == 42
    assert evidence["record_statistics"]["records_read"] == 10
    assert evidence["record_statistics"]["records_rejected"] == 2


def test_agent_execution_evidence_explains_failure_and_load_counts() -> None:
    started = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    run = WorkflowRun(
        execution_id="run-42",
        workflow_name="Metadata Import - Import Products",
        status=WorkflowStatus.FAILED,
        started_at=started,
        completed_at=started + timedelta(seconds=15),
        initiated_by="planner",
        initiated_by_display="Planning User",
        trigger_source=TriggerSource.AI_AGENT,
        oracle_execution_username="epm.integration",
        error_message="Metadata import failed.",
        steps=(
            WorkflowStepResult(
                name="Run metadata import",
                sequence=1,
                status=WorkflowStepStatus.FAILED,
                started_at=started,
                completed_at=started + timedelta(seconds=15),
                error_message="Invalid member in Entity dimension.",
                details={
                    "job_id": 917,
                    "password": "must-not-leak",
                    "output_path": "C:/private/import-errors.csv",
                    "record_statistics": {
                        "records_read": 10,
                        "records_processed": 8,
                        "records_rejected": 2,
                        "details": [
                            {
                                "dimension_name": "Entity",
                                "records_read": 10,
                                "records_processed": 8,
                                "records_rejected": 2,
                                "token": "must-not-leak",
                            }
                        ],
                    },
                },
            ),
        ),
    )

    evidence = agent_execution_evidence(run)

    assert evidence["execution_id"] == "run-42"
    assert evidence["status"] == "FAILED"
    assert evidence["executed_by"] == "epm.integration"
    assert evidence["record_statistics"]["records_rejected"] == 2
    assert evidence["steps"][0]["evidence"]["job_id"] == 917
    assert "password" not in evidence["steps"][0]["evidence"]
    assert evidence["artifacts"] == ["import-errors.csv"]
    assert evidence["diagnosis"] == (
        "Failed at Run metadata import. Oracle reported: "
        "Invalid member in Entity dimension."
    )
    assert "must-not-leak" not in str(evidence)


def test_agent_execution_evidence_reports_success_with_rejections() -> None:
    started = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    run = WorkflowRun(
        execution_id="run-43",
        workflow_name="Planning Data Import - Forecast",
        status=WorkflowStatus.SUCCESS,
        started_at=started,
        completed_at=started + timedelta(seconds=5),
        steps=(
            WorkflowStepResult(
                name="Import data",
                sequence=1,
                status=WorkflowStepStatus.SUCCESS,
                details={
                    "record_statistics": {
                        "records_read": 100,
                        "records_processed": 99,
                        "records_rejected": 1,
                        "details": [],
                    }
                },
            ),
        ),
    )

    evidence = agent_execution_evidence(run)

    assert evidence["diagnosis"] == (
        "Completed, but Oracle reported 1 rejected record."
    )


def test_agent_execution_evidence_explains_safe_flow_stop() -> None:
    started = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
    run = WorkflowRun(
        execution_id="run-cancelled",
        workflow_name="September Close",
        status=WorkflowStatus.CANCELLED,
        started_at=started,
        completed_at=started + timedelta(seconds=5),
        error_message=(
            "The user requested a safe stop. Completed Oracle steps were "
            "retained and remaining steps were not started."
        ),
    )

    evidence = agent_execution_evidence(run)

    assert evidence["status"] == "CANCELLED"
    assert evidence["diagnosis"] == run.error_message
