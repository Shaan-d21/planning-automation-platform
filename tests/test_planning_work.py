"""Tests for operational Planning cycles and dependency-aware tasks."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.application.planning_work import PlanningWorkApplicationService
from app.config.settings import Settings
from app.models.access_control import RoleCode
from app.models.workflow import WorkflowRun, WorkflowStatus
from app.models.planning_workflow import (
    CycleStageDraft,
    OperationalCycleStatus,
    PlanningCycleDraft,
    PlanningTaskDraft,
    PlanningTaskPriority,
    PlanningTaskReadiness,
    PlanningTaskStatus,
)
from app.models.planning_governance import PlanningApprovalStatus
from app.models.data_validation import DataQualityResult, DataQualityRules
from app.services.access_control_service import AccessControlService
from app.utils.exceptions import PlanningWorkflowError


def _context(tmp_path: Path):
    database = tmp_path / "planning-work.sqlite3"
    access = AccessControlService(database)
    administrator = access.bootstrap_administrator(
        username="admin",
        display_name="Test Administrator",
        email="admin@example.com",
        password="Test password 123!",
    )
    auditor = access.create_user(
        username="viewer",
        display_name="Planning Viewer",
        email="viewer@example.com",
        password="Viewer password 123!",
        roles=(RoleCode.VIEWER,),
        actor_user_id=administrator.user_id,
    )
    settings = Settings(
        epm_base_url="https://example.invalid",
        epm_username="oracle-user",
        epm_password="secret",
        application_name="Vision",
        workflow_database_file=database,
    )
    return PlanningWorkApplicationService(settings), administrator, auditor


def _draft() -> PlanningCycleDraft:
    return PlanningCycleDraft(
        code="AUG_FORECAST_FY27",
        name="August Forecast FY27",
        cycle_type="FORECAST",
        scenario="Forecast",
        year="FY27",
        actual_through_period="Jul",
        forecast_start_period="Aug",
        start_date=date(2027, 8, 3),
        due_date=date(2027, 8, 10),
        stages=(
            CycleStageDraft(
                code="ACTUAL_RECONCILIATION",
                name="Actual Reconciliation",
                sequence=1,
            ),
            CycleStageDraft(
                code="PLANNER_INPUT",
                name="Planner Input",
                sequence=2,
            ),
        ),
        tasks=(
            PlanningTaskDraft(
                key="RECONCILE_ACTUALS",
                stage_code="ACTUAL_RECONCILIATION",
                title="Reconcile July Actuals",
                description="Compare ERP and Planning totals.",
                task_type="DATA_VALIDATION",
                priority=PlanningTaskPriority.HIGH,
                assigned_role_code="SERVICE_ADMINISTRATOR",
                due_at=datetime(2027, 8, 3, 12, tzinfo=UTC),
                action_type="OPEN_MY_WORK",
            ),
            PlanningTaskDraft(
                key="UPDATE_FORECAST",
                stage_code="PLANNER_INPUT",
                title="Update August Forecast",
                description="Enter approved planning assumptions.",
                task_type="PLANNER_INPUT",
                priority=PlanningTaskPriority.NORMAL,
                assigned_role_code="SERVICE_ADMINISTRATOR",
                due_at=datetime(2027, 8, 8, 12, tzinfo=UTC),
                action_type="OPEN_FORM",
                depends_on=("RECONCILE_ACTUALS",),
            ),
        ),
    )


def test_cycle_tasks_are_assignment_aware_and_dependency_driven(
    tmp_path: Path,
) -> None:
    service, administrator, auditor = _context(tmp_path)

    cycle = service.create_cycle(_draft(), actor=administrator)
    initial = service.home_snapshot(actor=administrator)

    assert cycle.status is OperationalCycleStatus.OPEN
    assert [task.readiness for task in initial.tasks] == [
        PlanningTaskReadiness.READY,
        PlanningTaskReadiness.WAITING,
    ]
    assert service.home_snapshot(actor=auditor).tasks == ()

    with pytest.raises(PlanningWorkflowError, match="prerequisite"):
        service.update_task_status(
            initial.tasks[1].task_id,
            PlanningTaskStatus.IN_PROGRESS,
            actor=administrator,
        )

    service.update_task_status(
        initial.tasks[0].task_id,
        PlanningTaskStatus.COMPLETED,
        actor=administrator,
    )
    after_first = service.home_snapshot(actor=administrator)
    assert after_first.tasks[1].readiness is PlanningTaskReadiness.READY
    assert after_first.cycles[0].progress_percent == 50

    service.update_task_status(
        after_first.tasks[1].task_id,
        PlanningTaskStatus.COMPLETED,
        actor=administrator,
    )
    complete = service.home_snapshot(actor=administrator)
    assert complete.cycles[0].progress_percent == 100
    assert complete.cycles[0].cycle.status is OperationalCycleStatus.COMPLETED


def test_only_cycle_designers_can_create_operational_cycles(
    tmp_path: Path,
) -> None:
    service, _, auditor = _context(tmp_path)

    with pytest.raises(PlanningWorkflowError, match="does not permit"):
        service.create_cycle(_draft(), actor=auditor)


def test_cycle_creation_rejects_dependency_cycles_and_secret_fields(
    tmp_path: Path,
) -> None:
    service, administrator, _ = _context(tmp_path)
    first, second = _draft().tasks
    cyclic = replace(
        _draft(),
        tasks=(
            replace(first, depends_on=(second.key,)),
            second,
        ),
    )
    with pytest.raises(PlanningWorkflowError, match="cannot contain a cycle"):
        service.create_cycle(cyclic, actor=administrator)

    secret_draft = replace(
        _draft(),
        tasks=(
            replace(
                first,
                action_config={"password": "must-not-be-stored"},
            ),
        ),
    )
    with pytest.raises(PlanningWorkflowError, match="cannot store"):
        service.create_cycle(secret_draft, actor=administrator)


def test_submission_approval_return_and_resubmission_are_governed(
    tmp_path: Path,
) -> None:
    service, administrator, auditor = _context(tmp_path)
    submission = PlanningTaskDraft(
        key="SUBMIT_FORECAST",
        stage_code="PLANNER_INPUT",
        title="Submit forecast",
        description="Submit validated forecast assumptions.",
        task_type="SUBMISSION",
        priority=PlanningTaskPriority.HIGH,
        assigned_role_code="SERVICE_ADMINISTRATOR",
        action_type="SUBMIT_APPROVAL",
    )
    review = PlanningTaskDraft(
        key="REVIEW_FORECAST",
        stage_code="REVIEW",
        title="Review forecast",
        description="Review and decide the submitted forecast.",
        task_type="APPROVAL",
        priority=PlanningTaskPriority.HIGH,
        assigned_role_code="SERVICE_ADMINISTRATOR",
        action_type="REVIEW_APPROVAL",
        depends_on=(submission.key,),
    )
    draft = replace(
        _draft(),
        stages=(
            CycleStageDraft(code="PLANNER_INPUT", name="Planner Input", sequence=1),
            CycleStageDraft(code="REVIEW", name="Review & Approval", sequence=2),
        ),
        tasks=(submission, review),
    )
    cycle = service.create_cycle(draft, actor=administrator)

    approvals = service.submit_for_approval(
        service.cycle_detail(cycle.cycle_id, actor=administrator)[2][0].task_id,
        actor=administrator,
    )
    assert len(approvals) == 1
    assert approvals[0].status is PlanningApprovalStatus.PENDING
    assert service.approvals(actor=auditor) == ()
    assert service.notifications(actor=administrator)[0].event_type == (
        "APPROVAL_REQUESTED"
    )

    with pytest.raises(PlanningWorkflowError, match="Explain what must change"):
        service.decide_approval(
            approvals[0].approval_id,
            PlanningApprovalStatus.RETURNED,
            comment="",
            actor=administrator,
        )

    returned = service.decide_approval(
        approvals[0].approval_id,
        PlanningApprovalStatus.RETURNED,
        comment="Update the volume assumption.",
        actor=administrator,
    )
    assert returned.status is PlanningApprovalStatus.RETURNED
    tasks = service.cycle_detail(cycle.cycle_id, actor=administrator)[2]
    assert tasks[0].status is PlanningTaskStatus.IN_PROGRESS
    assert tasks[1].readiness is PlanningTaskReadiness.WAITING

    resubmitted = service.submit_for_approval(tasks[0].task_id, actor=administrator)
    approved = service.decide_approval(
        resubmitted[0].approval_id,
        PlanningApprovalStatus.APPROVED,
        comment="Reviewed and approved.",
        actor=administrator,
    )
    assert approved.status is PlanningApprovalStatus.APPROVED
    assert service.home_snapshot(actor=administrator).cycles[0].progress_percent == 100


def test_data_review_evidence_gates_completion_and_is_attached_to_approval(
    tmp_path: Path,
) -> None:
    service, administrator, _ = _context(tmp_path)
    validation = replace(
        _draft().tasks[0],
        key="VALIDATE_FORECAST",
        stage_code="PLANNER_INPUT",
        action_type="OPEN_DATA_REVIEW",
        title="Validate forecast",
    )
    submission = PlanningTaskDraft(
        key="SUBMIT_FORECAST",
        stage_code="PLANNER_INPUT",
        title="Submit forecast",
        description="Submit validated forecast.",
        task_type="SUBMISSION",
        priority=PlanningTaskPriority.HIGH,
        assigned_role_code="SERVICE_ADMINISTRATOR",
        action_type="SUBMIT_APPROVAL",
        depends_on=(validation.key,),
    )
    review = PlanningTaskDraft(
        key="REVIEW_FORECAST",
        stage_code="REVIEW",
        title="Review forecast",
        description="Review submitted evidence.",
        task_type="APPROVAL",
        priority=PlanningTaskPriority.HIGH,
        assigned_role_code="SERVICE_ADMINISTRATOR",
        action_type="REVIEW_APPROVAL",
        depends_on=(submission.key,),
    )
    cycle = service.create_cycle(
        replace(
            _draft(),
            stages=(
                CycleStageDraft(code="PLANNER_INPUT", name="Planner Input", sequence=1),
                CycleStageDraft(code="REVIEW", name="Review", sequence=2),
            ),
            tasks=(validation, submission, review),
        ),
        actor=administrator,
    )
    tasks = service.cycle_detail(cycle.cycle_id, actor=administrator)[2]
    validation_task = next(item for item in tasks if item.action_type == "OPEN_DATA_REVIEW")

    with pytest.raises(PlanningWorkflowError, match="Run the assigned Data Review"):
        service.update_task_status(
            validation_task.task_id,
            PlanningTaskStatus.COMPLETED,
            actor=administrator,
        )

    evidence = service.record_quality_validation(
        validation_task.task_id,
        selection={
            "cube": "Plan1",
            "pov": {"Scenario": "Forecast"},
            "rows": [{"dimension": "Account", "members": ["Revenue"]}],
            "columns": [{"dimension": "Period", "members": ["Jan"]}],
        },
        criteria={"check_missing": True},
        result=DataQualityResult(
            status="PASS",
            checked_cells=1,
            passed_cells=1,
            issue_count=0,
            missing_count=0,
            zero_count=0,
            below_minimum_count=0,
            above_maximum_count=0,
            non_numeric_count=0,
            issues=(),
            truncated=False,
            rules=DataQualityRules(),
        ),
        actor=administrator,
    )
    assert evidence.completion_allowed
    service.update_task_status(
        validation_task.task_id,
        PlanningTaskStatus.COMPLETED,
        actor=administrator,
    )
    tasks = service.cycle_detail(cycle.cycle_id, actor=administrator)[2]
    submission_task = next(item for item in tasks if item.action_type == "SUBMIT_APPROVAL")
    approval = service.submit_for_approval(
        submission_task.task_id,
        actor=administrator,
    )[0]

    assert approval.validation is not None
    assert approval.validation.validation_id == evidence.validation_id


def test_data_review_warning_requires_explicit_acknowledgement(
    tmp_path: Path,
) -> None:
    service, administrator, _ = _context(tmp_path)
    validation = replace(
        _draft().tasks[0],
        action_type="OPEN_DATA_REVIEW",
        action_config={
            "data_review": {
                "validation_type": "QUALITY",
                "slice": {
                    "cube": "Plan1",
                    "pov": {"Scenario": "Forecast"},
                    "rows": [{"dimension": "Account", "members": ["Revenue"]}],
                    "columns": [{"dimension": "Period", "members": ["Jan"]}],
                },
            }
        },
    )
    cycle = service.create_cycle(
        replace(
            _draft(),
            stages=(_draft().stages[0],),
            tasks=(validation,),
        ),
        actor=administrator,
    )
    task = service.cycle_detail(cycle.cycle_id, actor=administrator)[2][0]
    selection = validation.action_config["data_review"]["slice"]
    evidence = service.record_quality_validation(
        task.task_id,
        selection=selection,
        criteria={"check_zero": True},
        result=DataQualityResult(
            status="WARNING",
            checked_cells=1,
            passed_cells=0,
            issue_count=1,
            missing_count=0,
            zero_count=1,
            below_minimum_count=0,
            above_maximum_count=0,
            non_numeric_count=0,
            issues=(),
            truncated=False,
            rules=DataQualityRules(check_zero=True),
        ),
        actor=administrator,
    )

    with pytest.raises(PlanningWorkflowError, match="Acknowledge"):
        service.update_task_status(
            task.task_id,
            PlanningTaskStatus.COMPLETED,
            actor=administrator,
        )
    acknowledged = service.acknowledge_validation_warning(
        evidence.validation_id,
        actor=administrator,
    )
    assert acknowledged.completion_allowed
    assert service.update_task_status(
        task.task_id,
        PlanningTaskStatus.COMPLETED,
        actor=administrator,
    ).status is PlanningTaskStatus.COMPLETED


def test_oracle_attempts_control_task_completion_and_retain_retries(
    tmp_path: Path,
) -> None:
    service, administrator, auditor = _context(tmp_path)
    operation_task = replace(
        _draft().tasks[0],
        action_type="RUN_BUSINESS_RULE",
        title="Calculate forecast",
    )
    cycle = service.create_cycle(
        replace(
            _draft(),
            stages=(_draft().stages[0],),
            tasks=(operation_task,),
        ),
        actor=administrator,
    )
    task = service.cycle_detail(cycle.cycle_id, actor=administrator)[2][0]

    with pytest.raises(PlanningWorkflowError, match="completes only"):
        service.update_task_status(
            task.task_id,
            PlanningTaskStatus.COMPLETED,
            actor=administrator,
        )

    first = service.link_task_execution(
        task.task_id,
        "attempt-failed",
        action_type="RUN_BUSINESS_RULE",
        actor=administrator,
    )
    assert first.attempt_number == 1
    assert service.can_view_task_execution(
        first.execution_id,
        actor=administrator,
    )
    assert not service.can_view_task_execution(
        first.execution_id,
        actor=auditor,
    )
    service._workflow_repository.save(
        WorkflowRun(
            execution_id="attempt-failed",
            workflow_name="Business Rule - Calculate forecast",
            status=WorkflowStatus.FAILED,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            error_message="Oracle calculation failed.",
        )
    )
    failed_task = service.home_snapshot(actor=administrator).tasks[0]
    assert failed_task.status is PlanningTaskStatus.BLOCKED

    second = service.link_task_execution(
        task.task_id,
        "attempt-success",
        action_type="RUN_BUSINESS_RULE",
        actor=administrator,
    )
    assert second.attempt_number == 2
    service._workflow_repository.save(
        WorkflowRun(
            execution_id="attempt-success",
            workflow_name="Business Rule - Calculate forecast",
            status=WorkflowStatus.SUCCESS,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
    )
    completed = service.home_snapshot(actor=administrator).tasks[0]
    attempts = service.execution_attempts((completed,))[completed.task_id]

    assert completed.status is PlanningTaskStatus.COMPLETED
    assert [item.execution_id for item in attempts] == [
        "attempt-success",
        "attempt-failed",
    ]
    assert attempts[1].error_message == "Oracle calculation failed."


@pytest.mark.parametrize(
    "action_type",
    (
        "RUN_PIPELINE",
        "RUN_BUSINESS_RULE",
        "RUN_DATA_MAP",
        "RUN_DATA_INTEGRATION",
        "RUN_METADATA_IMPORT",
        "RUN_DATA_IMPORT",
        "RUN_CUBE_REFRESH",
        "UPDATE_SUBSTITUTION_VARIABLE",
    ),
)
def test_supported_oracle_task_actions_require_execution_outcomes(
    tmp_path: Path,
    action_type: str,
) -> None:
    service, administrator, _ = _context(tmp_path)
    task_draft = replace(_draft().tasks[0], action_type=action_type)
    cycle = service.create_cycle(
        replace(
            _draft(),
            stages=(_draft().stages[0],),
            tasks=(task_draft,),
        ),
        actor=administrator,
    )
    task = service.cycle_detail(cycle.cycle_id, actor=administrator)[2][0]

    assert service.authorize_task_execution(
        task.task_id,
        action_type=action_type,
        actor=administrator,
    ) == task
    with pytest.raises(PlanningWorkflowError, match="completes only"):
        service.update_task_status(
            task.task_id,
            PlanningTaskStatus.COMPLETED,
            actor=administrator,
        )
