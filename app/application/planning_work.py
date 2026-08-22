"""Application service for role-aware Planning cycles and user tasks."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from app.config.settings import Settings
from app.models.access_control import Permission, UserAccount
from app.models.planning_workflow import (
    CycleStageDraft,
    CycleStageStatus,
    PlanningCycleDraft,
    PlanningCycleRecord,
    PlanningCycleStage,
    PlanningTask,
    PlanningTaskDraft,
    PlanningTaskExecution,
    PlanningTaskReadiness,
    PlanningTaskStatus,
)
from app.models.planning_governance import (
    PlanningApproval,
    PlanningApprovalStatus,
    UserNotification,
)
from app.models.data_validation import DataQualityResult, DataValidationResult
from app.models.planning_validation import (
    PlanningValidationEvidence,
    PlanningValidationStatus,
    PlanningValidationType,
)
from app.services.planning_governance_repository import (
    PlanningGovernanceRepository,
)
from app.services.planning_work_repository import PlanningWorkRepository
from app.services.planning_validation_repository import PlanningValidationRepository
from app.services.workflow_repository import SQLWorkflowRepository
from app.utils.exceptions import PlanningWorkflowError


_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]*$")
_TERMINAL_TASK_STATUSES = {
    PlanningTaskStatus.COMPLETED,
    PlanningTaskStatus.CANCELLED,
}
_EXECUTABLE_TASK_ACTIONS = {
    "RUN_PIPELINE",
    "RUN_BUSINESS_RULE",
    "RUN_DATA_MAP",
    "RUN_DATA_INTEGRATION",
    "RUN_METADATA_IMPORT",
    "RUN_DATA_IMPORT",
    "RUN_CUBE_REFRESH",
    "UPDATE_SUBSTITUTION_VARIABLE",
}
_ALLOWED_TRANSITIONS = {
    PlanningTaskStatus.NOT_STARTED: {
        PlanningTaskStatus.IN_PROGRESS,
        PlanningTaskStatus.BLOCKED,
        PlanningTaskStatus.COMPLETED,
    },
    PlanningTaskStatus.IN_PROGRESS: {
        PlanningTaskStatus.NOT_STARTED,
        PlanningTaskStatus.BLOCKED,
        PlanningTaskStatus.COMPLETED,
    },
    PlanningTaskStatus.BLOCKED: {
        PlanningTaskStatus.NOT_STARTED,
        PlanningTaskStatus.IN_PROGRESS,
    },
    PlanningTaskStatus.COMPLETED: set(),
    PlanningTaskStatus.CANCELLED: set(),
}


@dataclass(frozen=True, slots=True)
class PlanningHomeSnapshot:
    """User-specific business context used by the future homepage."""

    cycles: tuple[PlanningCycleOverview, ...]
    tasks: tuple[PlanningTask, ...]
    action_required: int
    due_today: int
    overdue: int
    completed: int
    recent_activity: tuple[RecentPlanningActivity, ...]


@dataclass(frozen=True, slots=True)
class PlanningCycleOverview:
    """One cycle plus derived business-stage progress."""

    cycle: PlanningCycleRecord
    stages: tuple[PlanningCycleStage, ...]
    completed_stages: int
    progress_percent: int
    current_stage: PlanningCycleStage | None


@dataclass(frozen=True, slots=True)
class RecentPlanningActivity:
    """Permission-filtered execution activity for the role-aware homepage."""

    execution_id: str
    name: str
    status: str
    started_at: datetime
    initiated_by: str
    trigger_source: str


class PlanningWorkApplicationService:
    """Coordinate validation, authorization, and Planning work persistence."""

    def __init__(self, settings: Settings) -> None:
        self._repository = PlanningWorkRepository(settings.database_target)
        self._governance = PlanningGovernanceRepository(
            settings.database_target
        )
        self._workflow_repository = SQLWorkflowRepository(
            settings.database_target
        )
        self._validations = PlanningValidationRepository(
            settings.database_target
        )

    def create_cycle(
        self,
        draft: PlanningCycleDraft,
        *,
        actor: UserAccount,
    ) -> PlanningCycleRecord:
        """Create one complete cycle aggregate in a single transaction."""
        if not actor.has_permission(Permission.PROCESS_DESIGN):
            raise PlanningWorkflowError(
                "Your platform role does not permit Planning cycle design."
            )
        normalized = self._validated_draft(draft)
        return self._repository.create_cycle(
            normalized,
            created_by_user_id=actor.user_id,
        )

    def list_cycles(self, *, actor: UserAccount) -> tuple[PlanningCycleRecord, ...]:
        """Return active operational cycles visible to a signed-in user."""
        if not actor.active:
            raise PlanningWorkflowError("The platform user is inactive.")
        return self._repository.list_cycles()

    def cycle_detail(
        self,
        cycle_id: int,
        *,
        actor: UserAccount,
    ) -> tuple[
        PlanningCycleRecord,
        tuple[PlanningCycleStage, ...],
        tuple[PlanningTask, ...],
    ]:
        self._repository.reconcile_task_executions()
        cycle = self._repository.require_cycle(cycle_id)
        return (
            cycle,
            self._repository.list_stages(cycle_id),
            (
                self._repository.list_tasks(cycle_id)
                if actor.has_permission(Permission.PROCESS_DESIGN)
                else self._repository.list_tasks_for_user(
                    actor,
                    cycle_id=cycle_id,
                )
            ),
        )

    def home_snapshot(self, *, actor: UserAccount) -> PlanningHomeSnapshot:
        """Return priorities and cycle progress without querying Oracle live."""
        self._repository.reconcile_task_executions()
        cycles = tuple(
            self._overview(cycle)
            for cycle in self.list_cycles(actor=actor)
        )
        tasks = self._repository.list_tasks_for_user(actor)
        recent_runs = self._workflow_repository.list_recent(limit=12)
        if not actor.has_permission(Permission.HISTORY_VIEW):
            actor_names = {
                actor.username.casefold(),
                actor.display_name.casefold(),
            }
            recent_runs = tuple(
                run
                for run in recent_runs
                if str(run.initiated_by or "").casefold() in actor_names
                or str(run.initiated_by_display or "").casefold()
                in actor_names
            )
        today = datetime.now(UTC).date()
        actionable = tuple(
            task
            for task in tasks
            if task.status not in _TERMINAL_TASK_STATUSES
            and task.readiness is not PlanningTaskReadiness.WAITING
        )
        return PlanningHomeSnapshot(
            cycles=cycles,
            tasks=tasks,
            action_required=len(actionable),
            due_today=sum(
                1
                for task in tasks
                if task.due_at
                and task.due_at.date() == today
                and task.status not in _TERMINAL_TASK_STATUSES
            ),
            overdue=sum(
                1
                for task in tasks
                if task.due_at
                and task.due_at.date() < today
                and task.status not in _TERMINAL_TASK_STATUSES
            ),
            completed=sum(
                1
                for task in tasks
                if task.status is PlanningTaskStatus.COMPLETED
            ),
            recent_activity=tuple(
                RecentPlanningActivity(
                    execution_id=run.execution_id,
                    name=run.workflow_name,
                    status=run.status.value,
                    started_at=run.started_at,
                    initiated_by=(
                        run.initiated_by_display
                        or run.initiated_by
                        or "System"
                    ),
                    trigger_source=run.trigger_source.value,
                )
                for run in recent_runs[:8]
            ),
        )

    def assigned_work(
        self,
        *,
        actor: UserAccount,
        cycle_id: int | None = None,
    ) -> tuple[tuple[PlanningCycleOverview, ...], tuple[PlanningTask, ...]]:
        """Return the complete task workspace visible to one user."""
        if not actor.active:
            raise PlanningWorkflowError("The platform user is inactive.")
        self._repository.reconcile_task_executions()
        cycles = tuple(
            self._overview(cycle)
            for cycle in self.list_cycles(actor=actor)
            if cycle_id is None or cycle.cycle_id == cycle_id
        )
        return cycles, self._repository.list_tasks_for_user(
            actor,
            cycle_id=cycle_id,
        )

    def administration_workspace(
        self,
        *,
        actor: UserAccount,
    ) -> tuple[PlanningCycleOverview, ...]:
        """Return complete cycle progress for an authorized cycle designer."""
        if not actor.has_permission(Permission.PROCESS_DESIGN):
            raise PlanningWorkflowError(
                "Your platform role does not permit Planning cycle design."
            )
        return tuple(
            self._overview(cycle)
            for cycle in self.list_cycles(actor=actor)
        )

    def _overview(self, cycle: PlanningCycleRecord) -> PlanningCycleOverview:
        stages = self._repository.list_stages(cycle.cycle_id)
        completed = sum(
            1
            for stage in stages
            if stage.status
            in {CycleStageStatus.COMPLETED, CycleStageStatus.SKIPPED}
        )
        current = next(
            (
                stage
                for stage in stages
                if stage.status
                not in {CycleStageStatus.COMPLETED, CycleStageStatus.SKIPPED}
            ),
            None,
        )
        return PlanningCycleOverview(
            cycle=cycle,
            stages=stages,
            completed_stages=completed,
            progress_percent=(
                round((completed / len(stages)) * 100) if stages else 0
            ),
            current_stage=current,
        )

    def update_task_status(
        self,
        task_id: int,
        status: PlanningTaskStatus,
        *,
        actor: UserAccount,
    ) -> PlanningTask:
        """Apply a governed task transition by its assignee or administrator."""
        task = self._repository.require_task(task_id)
        if not self._can_manage_task(task, actor):
            raise PlanningWorkflowError(
                "This task is not assigned to you or one of your roles."
            )
        if task.action_type == "REVIEW_APPROVAL" and status is not PlanningTaskStatus.CANCELLED:
            raise PlanningWorkflowError(
                "Use the Approvals workspace to approve or return this submission."
            )
        if task.action_type == "SUBMIT_APPROVAL" and status is PlanningTaskStatus.COMPLETED:
            raise PlanningWorkflowError(
                "Use Submit for review so the reviewer receives a governed approval request."
            )
        if (
            task.action_type in _EXECUTABLE_TASK_ACTIONS
            and status is not PlanningTaskStatus.CANCELLED
        ):
            raise PlanningWorkflowError(
                "Run the assigned Oracle operation. This task completes only "
                "after its retained execution succeeds."
            )
        if (
            task.action_type == "OPEN_DATA_REVIEW"
            and status is PlanningTaskStatus.COMPLETED
        ):
            evidence = self._validations.latest_for_task(task_id)
            if evidence is None:
                raise PlanningWorkflowError(
                    "Run the assigned Data Review validation before completing this task."
                )
            if evidence.status is PlanningValidationStatus.FAIL:
                raise PlanningWorkflowError(
                    "Resolve the failed Data Review checks and run validation again."
                )
            if not evidence.completion_allowed:
                raise PlanningWorkflowError(
                    "Acknowledge the latest Data Review warnings before completing this task."
                )
        if status is PlanningTaskStatus.CANCELLED:
            if not actor.has_permission(Permission.PROCESS_DESIGN):
                raise PlanningWorkflowError(
                    "Only a Planning cycle designer can cancel a task."
                )
        elif status not in _ALLOWED_TRANSITIONS[task.status]:
            raise PlanningWorkflowError(
                f"Task status cannot change from {task.status.value} to "
                f"{status.value}."
            )
        if (
            task.incomplete_dependency_ids
            and status
            in {PlanningTaskStatus.IN_PROGRESS, PlanningTaskStatus.COMPLETED}
        ):
            raise PlanningWorkflowError(
                "Complete the prerequisite tasks before starting this task."
            )
        return self._repository.update_task_status(task_id, status)

    def data_review_context(
        self, task_id: int, *, actor: UserAccount
    ) -> tuple[PlanningTask, PlanningValidationEvidence | None]:
        """Return authorized task configuration and its latest evidence."""
        task = self._authorize_data_review(
            task_id,
            actor=actor,
            allow_terminal=True,
        )
        return task, self._validations.latest_for_task(task_id)

    def validation_evidence(
        self, tasks: tuple[PlanningTask, ...]
    ) -> dict[int, PlanningValidationEvidence]:
        """Return only the latest compact evidence for each visible task."""
        return self._validations.latest_for_tasks(
            tuple(task.task_id for task in tasks)
        )

    def record_quality_validation(
        self,
        task_id: int,
        *,
        selection: dict[str, Any],
        criteria: dict[str, Any],
        result: DataQualityResult,
        actor: UserAccount,
    ) -> PlanningValidationEvidence:
        task = self._authorize_data_review(task_id, actor=actor)
        self._assert_configured_selection(task, selection, "QUALITY")
        return self._validations.record(
            task_id=task_id,
            validation_type=PlanningValidationType.QUALITY,
            status=PlanningValidationStatus(result.status),
            source_cube=str(selection["cube"]),
            target_cube=None,
            selection=selection,
            criteria=criteria,
            checked_cells=result.checked_cells,
            matched_cells=None,
            exception_count=result.issue_count,
            warning_count=result.zero_count,
            performed_by_user_id=actor.user_id,
        )

    def record_comparison_validation(
        self,
        task_id: int,
        *,
        selection: dict[str, Any],
        target_cube: str,
        criteria: dict[str, Any],
        result: DataValidationResult,
        actor: UserAccount,
    ) -> PlanningValidationEvidence:
        task = self._authorize_data_review(task_id, actor=actor)
        self._assert_configured_selection(task, selection, "COMPARISON")
        status = (
            PlanningValidationStatus.PASS
            if result.is_successful
            else PlanningValidationStatus.FAIL
        )
        return self._validations.record(
            task_id=task_id,
            validation_type=PlanningValidationType.COMPARISON,
            status=status,
            source_cube=str(selection["cube"]),
            target_cube=target_cube,
            selection=selection,
            criteria=criteria,
            checked_cells=result.compared_cells,
            matched_cells=result.matched_cells,
            exception_count=result.compared_cells - result.matched_cells,
            warning_count=0,
            performed_by_user_id=actor.user_id,
        )

    def acknowledge_validation_warning(
        self, validation_id: int, *, actor: UserAccount
    ) -> PlanningValidationEvidence:
        evidence = self._validations.require(validation_id)
        self._authorize_data_review(evidence.task_id, actor=actor)
        latest = self._validations.latest_for_task(evidence.task_id)
        if latest is None or latest.validation_id != validation_id:
            raise PlanningWorkflowError(
                "Only the latest Data Review validation can be acknowledged."
            )
        return self._validations.acknowledge_warning(
            validation_id,
            user_id=actor.user_id,
        )

    def _authorize_data_review(
        self,
        task_id: int,
        *,
        actor: UserAccount,
        allow_terminal: bool = False,
    ) -> PlanningTask:
        task = self._repository.require_task(task_id)
        if not self._can_manage_task(task, actor):
            raise PlanningWorkflowError(
                "This task is not assigned to you or one of your roles."
            )
        if task.action_type != "OPEN_DATA_REVIEW":
            raise PlanningWorkflowError(
                "This task is not configured for governed Data Review."
            )
        if not allow_terminal and task.status in _TERMINAL_TASK_STATUSES:
            raise PlanningWorkflowError(
                "This Data Review task is already complete or cancelled."
            )
        if not allow_terminal and task.incomplete_dependency_ids:
            raise PlanningWorkflowError(
                "Complete the prerequisite tasks before validating Planning data."
            )
        return task

    @staticmethod
    def _assert_configured_selection(
        task: PlanningTask,
        selection: dict[str, Any],
        validation_type: str,
    ) -> None:
        configured = task.action_config.get("data_review")
        if not isinstance(configured, dict):
            return
        expected = configured.get("slice")
        if isinstance(expected, dict) and expected != selection:
            raise PlanningWorkflowError(
                "The reviewed cube intersection does not match this assigned task. Reopen the task from My Work to restore its configured layout."
            )
        expected_type = str(configured.get("validation_type", "")).upper()
        if expected_type and expected_type != validation_type:
            raise PlanningWorkflowError(
                f"This task requires {expected_type.lower()} validation."
            )

    def authorize_task_execution(
        self,
        task_id: int,
        *,
        action_type: str,
        actor: UserAccount,
    ) -> PlanningTask:
        """Authorize a governed Oracle operation before it is submitted."""
        task = self._repository.require_task(task_id)
        if not self._can_manage_task(task, actor):
            raise PlanningWorkflowError(
                "This task is not assigned to you or one of your roles."
            )
        if task.action_type != action_type:
            raise PlanningWorkflowError(
                "This task is configured for a different Planning action."
            )
        if task.status in _TERMINAL_TASK_STATUSES:
            raise PlanningWorkflowError(
                "This task is already complete or cancelled."
            )
        if task.incomplete_dependency_ids:
            raise PlanningWorkflowError(
                "Complete the prerequisite tasks before running this operation."
            )
        return task

    def link_task_execution(
        self,
        task_id: int,
        execution_id: str,
        *,
        action_type: str,
        actor: UserAccount,
    ) -> PlanningTaskExecution:
        """Retain one authorized Oracle attempt against its business task."""
        self.authorize_task_execution(
            task_id,
            action_type=action_type,
            actor=actor,
        )
        normalized_execution_id = str(execution_id).strip()
        if not normalized_execution_id or len(normalized_execution_id) > 64:
            raise PlanningWorkflowError("Oracle execution ID is invalid.")
        return self._repository.link_task_execution(
            task_id,
            normalized_execution_id,
            initiated_by_user_id=actor.user_id,
        )

    def execution_attempts(
        self,
        tasks: tuple[PlanningTask, ...],
    ) -> dict[int, tuple[PlanningTaskExecution, ...]]:
        """Return retained attempts for an already authorized task collection."""
        return self._repository.list_task_executions(
            tuple(task.task_id for task in tasks)
        )

    def can_view_task_execution(
        self,
        execution_id: str,
        *,
        actor: UserAccount,
    ) -> bool:
        """Return whether an execution belongs to one of the actor's tasks."""
        task_id = self._repository.task_id_for_execution(
            str(execution_id).strip()
        )
        if task_id is None:
            return False
        return self._can_manage_task(self._repository.require_task(task_id), actor)

    def submit_for_approval(
        self,
        task_id: int,
        *,
        actor: UserAccount,
    ) -> tuple[PlanningApproval, ...]:
        """Complete a submission task and atomically open reviewer work."""
        task = self._repository.require_task(task_id)
        if not self._can_manage_task(task, actor):
            raise PlanningWorkflowError(
                "This task is not assigned to you or one of your roles."
            )
        if task.incomplete_dependency_ids:
            raise PlanningWorkflowError(
                "Complete the prerequisite tasks before submitting for approval."
            )
        return self._governance.submit_for_approval(
            task_id,
            submitted_by_user_id=actor.user_id,
        )

    def approvals(self, *, actor: UserAccount) -> tuple[PlanningApproval, ...]:
        """Return only review requests assigned to the current user or roles."""
        if not actor.active:
            raise PlanningWorkflowError("The platform user is inactive.")
        return self._governance.list_approvals_for_user(actor)

    def decide_approval(
        self,
        approval_id: int,
        decision: PlanningApprovalStatus,
        *,
        comment: str | None,
        actor: UserAccount,
    ) -> PlanningApproval:
        """Approve or return one assigned review request."""
        approval = self._governance.require_approval(approval_id)
        review_task = self._repository.require_task(approval.approval_task_id)
        if not self._can_manage_task(review_task, actor):
            raise PlanningWorkflowError(
                "This approval is not assigned to you or one of your roles."
            )
        normalized_comment = str(comment or "").strip()
        if decision is PlanningApprovalStatus.RETURNED and not normalized_comment:
            raise PlanningWorkflowError(
                "Explain what must change before returning a submission."
            )
        if len(normalized_comment) > 4000:
            raise PlanningWorkflowError(
                "Approval comments cannot exceed 4000 characters."
            )
        return self._governance.decide(
            approval_id,
            decision=decision,
            comment=normalized_comment or None,
            decided_by_user_id=actor.user_id,
        )

    def notifications(self, *, actor: UserAccount) -> tuple[UserNotification, ...]:
        """Return the current user's notification inbox."""
        if not actor.active:
            raise PlanningWorkflowError("The platform user is inactive.")
        return self._governance.list_notifications(actor.user_id)

    def read_notification(
        self,
        notification_id: int,
        *,
        actor: UserAccount,
    ) -> UserNotification:
        return self._governance.mark_notification_read(
            notification_id,
            user_id=actor.user_id,
        )

    def read_all_notifications(self, *, actor: UserAccount) -> None:
        self._governance.mark_all_notifications_read(user_id=actor.user_id)

    @staticmethod
    def _can_manage_task(task: PlanningTask, actor: UserAccount) -> bool:
        if actor.has_permission(Permission.PROCESS_DESIGN):
            return True
        if task.assigned_user_id == actor.user_id:
            return True
        role_codes = {role.value for role in actor.roles}
        return bool(
            task.assigned_role_code
            and task.assigned_role_code in role_codes
        )

    def _validated_draft(self, draft: PlanningCycleDraft) -> PlanningCycleDraft:
        code = self._code(draft.code, "Planning cycle code")
        name = self._text(draft.name, "Planning cycle name", 200)
        cycle_type = self._code(draft.cycle_type, "Planning cycle type")
        year = self._text(draft.year, "Planning year", 40)
        if draft.due_date < draft.start_date:
            raise PlanningWorkflowError(
                "Planning cycle due date cannot be before its start date."
            )
        if not draft.stages:
            raise PlanningWorkflowError(
                "A Planning cycle requires at least one business stage."
            )
        stage_codes: set[str] = set()
        stage_sequences: set[int] = set()
        stages: list[CycleStageDraft] = []
        for stage in draft.stages:
            stage_code = self._code(stage.code, "Stage code")
            if stage_code in stage_codes or stage.sequence in stage_sequences:
                raise PlanningWorkflowError(
                    "Stage codes and sequence numbers must be unique."
                )
            if stage.sequence <= 0:
                raise PlanningWorkflowError(
                    "Stage sequence numbers must be greater than zero."
                )
            if (
                stage.start_date
                and stage.due_date
                and stage.due_date < stage.start_date
            ):
                raise PlanningWorkflowError(
                    f"Stage '{stage_code}' has an invalid date range."
                )
            stage_codes.add(stage_code)
            stage_sequences.add(stage.sequence)
            stages.append(
                replace(
                    stage,
                    code=stage_code,
                    name=self._text(stage.name, "Stage name", 160),
                )
            )

        if not draft.tasks:
            raise PlanningWorkflowError(
                "A Planning cycle requires at least one actionable task."
            )
        task_keys: set[str] = set()
        tasks: list[PlanningTaskDraft] = []
        for task in draft.tasks:
            key = self._code(task.key, "Task key")
            stage_code = self._code(task.stage_code, "Task stage code")
            if key in task_keys:
                raise PlanningWorkflowError("Task keys must be unique.")
            if stage_code not in stage_codes:
                raise PlanningWorkflowError(
                    f"Task '{key}' references unknown stage '{stage_code}'."
                )
            if bool(task.assigned_username) == bool(task.assigned_role_code):
                raise PlanningWorkflowError(
                    f"Task '{key}' must have exactly one user or role assignee."
                )
            if task.due_at and task.due_at.tzinfo is None:
                raise PlanningWorkflowError(
                    f"Task '{key}' due time must include a timezone."
                )
            self._reject_secrets(task.action_config, task_key=key)
            task_keys.add(key)
            tasks.append(
                replace(
                    task,
                    key=key,
                    stage_code=stage_code,
                    title=self._text(task.title, "Task title", 200),
                    task_type=self._code(task.task_type, "Task type"),
                    action_type=self._code(task.action_type, "Task action type"),
                    assigned_username=(
                        task.assigned_username.strip()
                        if task.assigned_username
                        else None
                    ),
                    assigned_role_code=(
                        self._code(task.assigned_role_code, "Assigned role")
                        if task.assigned_role_code
                        else None
                    ),
                    scenario=task.scenario or draft.scenario,
                    depends_on=tuple(
                        self._code(value, "Task dependency")
                        for value in task.depends_on
                    ),
                )
            )
        self._validate_dependencies(tasks, task_keys)
        return replace(
            draft,
            code=code,
            name=name,
            cycle_type=cycle_type,
            year=year,
            process_code=(
                self._code(draft.process_code, "Process code")
                if draft.process_code
                else None
            ),
            stages=tuple(sorted(stages, key=lambda item: item.sequence)),
            tasks=tuple(tasks),
        )

    @staticmethod
    def _validate_dependencies(
        tasks: list[PlanningTaskDraft],
        task_keys: set[str],
    ) -> None:
        graph = {task.key: set(task.depends_on) for task in tasks}
        for task in tasks:
            unknown = set(task.depends_on) - task_keys
            if unknown:
                raise PlanningWorkflowError(
                    f"Task '{task.key}' has unknown dependencies: "
                    + ", ".join(sorted(unknown))
                )
            if task.key in task.depends_on:
                raise PlanningWorkflowError(
                    f"Task '{task.key}' cannot depend on itself."
                )
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise PlanningWorkflowError(
                    "Planning task dependencies cannot contain a cycle."
                )
            if key in visited:
                return
            visiting.add(key)
            for dependency in graph[key]:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for key in graph:
            visit(key)

    @staticmethod
    def _reject_secrets(value: Any, *, task_key: str) -> None:
        forbidden = {"password", "secret", "token", "api_key", "apikey"}
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized = str(key).strip().casefold()
                if normalized in forbidden:
                    raise PlanningWorkflowError(
                        f"Task '{task_key}' action configuration cannot store "
                        f"the secret field '{key}'."
                    )
                PlanningWorkApplicationService._reject_secrets(
                    nested,
                    task_key=task_key,
                )
        elif isinstance(value, list):
            for nested in value:
                PlanningWorkApplicationService._reject_secrets(
                    nested,
                    task_key=task_key,
                )

    @staticmethod
    def _code(value: str | None, label: str) -> str:
        normalized = str(value or "").strip().upper()
        if not normalized or not _CODE_PATTERN.fullmatch(normalized):
            raise PlanningWorkflowError(
                f"{label} must contain only letters, numbers, underscores, "
                "or hyphens."
            )
        return normalized

    @staticmethod
    def _text(value: str, label: str, maximum: int) -> str:
        normalized = str(value).strip()
        if not normalized or len(normalized) > maximum:
            raise PlanningWorkflowError(
                f"{label} is required and cannot exceed {maximum} characters."
            )
        return normalized
