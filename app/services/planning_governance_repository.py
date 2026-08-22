"""PostgreSQL repository for Planning approvals and user notifications."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, func, insert, or_, select, update

from app.infrastructure.database.engine import DatabaseTarget, database_for, utc_datetime
from app.infrastructure.database.schema import (
    planning_approvals,
    planning_cycle_stages,
    planning_cycles,
    planning_task_dependencies,
    planning_task_validations,
    planning_tasks,
    platform_roles,
    platform_user_roles,
    platform_users,
    user_notifications,
)
from app.models.access_control import UserAccount
from app.models.planning_governance import (
    NotificationSeverity,
    PlanningApproval,
    PlanningApprovalStatus,
    UserNotification,
)
from app.models.planning_workflow import PlanningTaskStatus
from app.services.planning_work_repository import PlanningWorkRepository
from app.utils.exceptions import PlanningWorkflowError


class PlanningGovernanceRepository:
    """Persist approval decisions and per-recipient notification state."""

    def __init__(self, database_target: DatabaseTarget) -> None:
        self._database = database_for(database_target)

    def submit_for_approval(
        self,
        task_id: int,
        *,
        submitted_by_user_id: int,
    ) -> tuple[PlanningApproval, ...]:
        now = datetime.now(UTC)
        approval_ids: list[int] = []
        with self._database.begin() as connection:
            task = connection.execute(
                select(
                    planning_tasks.c.task_id,
                    planning_tasks.c.stage_id,
                    planning_tasks.c.title,
                    planning_tasks.c.status,
                    planning_tasks.c.action_type,
                    planning_cycle_stages.c.cycle_id,
                    planning_cycles.c.name.label("cycle_name"),
                )
                .join(
                    planning_cycle_stages,
                    planning_cycle_stages.c.stage_id == planning_tasks.c.stage_id,
                )
                .join(
                    planning_cycles,
                    planning_cycles.c.cycle_id == planning_cycle_stages.c.cycle_id,
                )
                .where(planning_tasks.c.task_id == task_id)
                .with_for_update()
            ).mappings().one_or_none()
            if task is None:
                raise PlanningWorkflowError(f"Planning task {task_id} was not found.")
            if task["action_type"] != "SUBMIT_APPROVAL":
                raise PlanningWorkflowError(
                    "This responsibility is not configured to submit for approval."
                )
            if task["status"] in {
                PlanningTaskStatus.COMPLETED.value,
                PlanningTaskStatus.CANCELLED.value,
            }:
                raise PlanningWorkflowError(
                    "This responsibility has already been submitted or cancelled."
                )
            incomplete = connection.execute(
                select(func.count())
                .select_from(planning_task_dependencies)
                .join(
                    planning_tasks,
                    planning_tasks.c.task_id
                    == planning_task_dependencies.c.depends_on_task_id,
                )
                .where(
                    planning_task_dependencies.c.task_id == task_id,
                    planning_tasks.c.status != PlanningTaskStatus.COMPLETED.value,
                )
            ).scalar_one()
            if int(incomplete):
                raise PlanningWorkflowError(
                    "Complete the prerequisite tasks before submitting for approval."
                )

            review_tasks = connection.execute(
                select(
                    planning_tasks.c.task_id,
                    planning_tasks.c.title,
                    planning_tasks.c.assigned_user_id,
                    planning_tasks.c.assigned_role_id,
                )
                .join(
                    planning_task_dependencies,
                    planning_task_dependencies.c.task_id == planning_tasks.c.task_id,
                )
                .where(
                    planning_task_dependencies.c.depends_on_task_id == task_id,
                    planning_tasks.c.action_type == "REVIEW_APPROVAL",
                )
            ).mappings().all()
            if not review_tasks:
                raise PlanningWorkflowError(
                    "No reviewer responsibility depends on this submission. Ask a Planning administrator to add a Review approval responsibility."
                )

            connection.execute(
                update(planning_tasks)
                .where(planning_tasks.c.task_id == task_id)
                .values(
                    status=PlanningTaskStatus.COMPLETED.value,
                    completed_at=now,
                    updated_at=now,
                )
            )
            PlanningWorkRepository._recalculate_progress(
                connection,
                int(task["stage_id"]),
                int(task["cycle_id"]),
                now,
            )
            for review in review_tasks:
                validation_id = connection.execute(
                    select(planning_task_validations.c.validation_id)
                    .join(
                        planning_task_dependencies,
                        planning_task_dependencies.c.depends_on_task_id
                        == planning_task_validations.c.task_id,
                    )
                    .where(planning_task_dependencies.c.task_id == task_id)
                    .order_by(
                        planning_task_validations.c.performed_at.desc(),
                        planning_task_validations.c.validation_id.desc(),
                    )
                    .limit(1)
                ).scalar_one_or_none()
                existing = connection.execute(
                    select(planning_approvals.c.approval_id).where(
                        planning_approvals.c.approval_task_id == review["task_id"],
                        planning_approvals.c.status == PlanningApprovalStatus.PENDING.value,
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    raise PlanningWorkflowError(
                        "A review request is already pending for this responsibility."
                    )
                approval_id = int(
                    connection.execute(
                        insert(planning_approvals)
                        .values(
                            cycle_id=task["cycle_id"],
                            submitted_task_id=task_id,
                            approval_task_id=review["task_id"],
                            validation_id=validation_id,
                            status=PlanningApprovalStatus.PENDING.value,
                            submitted_by_user_id=submitted_by_user_id,
                            submitted_at=now,
                            created_at=now,
                            updated_at=now,
                        )
                        .returning(planning_approvals.c.approval_id)
                    ).scalar_one()
                )
                approval_ids.append(approval_id)
                self._notify_assignees(
                    connection,
                    assigned_user_id=review["assigned_user_id"],
                    assigned_role_id=review["assigned_role_id"],
                    event_type="APPROVAL_REQUESTED",
                    severity=NotificationSeverity.WARNING,
                    title=str(review["title"]),
                    message=f"{task['title']} was submitted for your review in {task['cycle_name']}.",
                    action_url="#approvals",
                    source_type="PLANNING_APPROVAL",
                    source_id=str(approval_id),
                    created_at=now,
                )
        return tuple(self.require_approval(approval_id) for approval_id in approval_ids)

    def list_approvals_for_user(self, user: UserAccount) -> tuple[PlanningApproval, ...]:
        role_codes = [role.value for role in user.roles]
        approval_task = planning_tasks.alias("approval_task")
        with self._database.connect() as connection:
            rows = connection.execute(
                self._approval_select(approval_task)
                .outerjoin(
                    platform_roles,
                    platform_roles.c.role_id == approval_task.c.assigned_role_id,
                )
                .where(
                    or_(
                        approval_task.c.assigned_user_id == user.user_id,
                        platform_roles.c.code.in_(role_codes),
                    )
                )
                .order_by(
                    (planning_approvals.c.status == PlanningApprovalStatus.PENDING.value).desc(),
                    planning_approvals.c.submitted_at.desc(),
                )
            ).mappings().all()
        return tuple(self._approval(row) for row in rows)

    def require_approval(self, approval_id: int) -> PlanningApproval:
        approval_task = planning_tasks.alias("approval_task")
        with self._database.connect() as connection:
            row = connection.execute(
                self._approval_select(approval_task).where(
                    planning_approvals.c.approval_id == approval_id
                )
            ).mappings().one_or_none()
        if row is None:
            raise PlanningWorkflowError(f"Planning approval {approval_id} was not found.")
        return self._approval(row)

    def decide(
        self,
        approval_id: int,
        *,
        decision: PlanningApprovalStatus,
        comment: str | None,
        decided_by_user_id: int,
    ) -> PlanningApproval:
        if decision not in {PlanningApprovalStatus.APPROVED, PlanningApprovalStatus.RETURNED}:
            raise PlanningWorkflowError("Approval decision must be Approved or Returned.")
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            row = connection.execute(
                select(planning_approvals).where(
                    planning_approvals.c.approval_id == approval_id
                ).with_for_update()
            ).mappings().one_or_none()
            if row is None:
                raise PlanningWorkflowError(f"Planning approval {approval_id} was not found.")
            if row["status"] != PlanningApprovalStatus.PENDING.value:
                raise PlanningWorkflowError("This review request has already been decided.")
            connection.execute(
                update(planning_approvals)
                .where(planning_approvals.c.approval_id == approval_id)
                .values(
                    status=decision.value,
                    decided_by_user_id=decided_by_user_id,
                    decided_at=now,
                    decision_comment=comment,
                    updated_at=now,
                )
            )
            submitted = connection.execute(
                select(planning_tasks.c.stage_id).where(
                    planning_tasks.c.task_id == row["submitted_task_id"]
                )
            ).one()
            review = connection.execute(
                select(planning_tasks.c.stage_id).where(
                    planning_tasks.c.task_id == row["approval_task_id"]
                )
            ).one()
            if decision is PlanningApprovalStatus.APPROVED:
                connection.execute(
                    update(planning_tasks)
                    .where(planning_tasks.c.task_id == row["approval_task_id"])
                    .values(
                        status=PlanningTaskStatus.COMPLETED.value,
                        completed_at=now,
                        updated_at=now,
                    )
                )
                severity = NotificationSeverity.SUCCESS
                title = "Planning submission approved"
                message = "Your Planning submission was approved."
            else:
                connection.execute(
                    update(planning_tasks)
                    .where(planning_tasks.c.task_id == row["submitted_task_id"])
                    .values(
                        status=PlanningTaskStatus.IN_PROGRESS.value,
                        completed_at=None,
                        updated_at=now,
                    )
                )
                connection.execute(
                    update(planning_tasks)
                    .where(planning_tasks.c.task_id == row["approval_task_id"])
                    .values(
                        status=PlanningTaskStatus.NOT_STARTED.value,
                        completed_at=None,
                        updated_at=now,
                    )
                )
                severity = NotificationSeverity.WARNING
                title = "Planning submission returned"
                message = comment or "Your Planning submission requires changes before it can be approved."
            PlanningWorkRepository._recalculate_progress(
                connection, int(submitted.stage_id), int(row["cycle_id"]), now
            )
            if int(review.stage_id) != int(submitted.stage_id):
                PlanningWorkRepository._recalculate_progress(
                    connection, int(review.stage_id), int(row["cycle_id"]), now
                )
            connection.execute(
                insert(user_notifications).values(
                    recipient_user_id=row["submitted_by_user_id"],
                    event_type=f"APPROVAL_{decision.value}",
                    severity=severity.value,
                    title=title,
                    message=message,
                    action_url="#tasks",
                    source_type="PLANNING_APPROVAL",
                    source_id=str(approval_id),
                    created_at=now,
                )
            )
        return self.require_approval(approval_id)

    def list_notifications(self, user_id: int, *, limit: int = 100) -> tuple[UserNotification, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                select(user_notifications)
                .where(user_notifications.c.recipient_user_id == user_id)
                .order_by(user_notifications.c.created_at.desc())
                .limit(limit)
            ).mappings().all()
        return tuple(self._notification(row) for row in rows)

    def mark_notification_read(self, notification_id: int, *, user_id: int) -> UserNotification:
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            changed = connection.execute(
                update(user_notifications)
                .where(
                    user_notifications.c.notification_id == notification_id,
                    user_notifications.c.recipient_user_id == user_id,
                )
                .values(read_at=func.coalesce(user_notifications.c.read_at, now))
                .returning(user_notifications)
            ).mappings().one_or_none()
        if changed is None:
            raise PlanningWorkflowError(f"Notification {notification_id} was not found.")
        return self._notification(changed)

    def mark_all_notifications_read(self, *, user_id: int) -> None:
        with self._database.begin() as connection:
            connection.execute(
                update(user_notifications)
                .where(
                    user_notifications.c.recipient_user_id == user_id,
                    user_notifications.c.read_at.is_(None),
                )
                .values(read_at=datetime.now(UTC))
            )

    @staticmethod
    def _approval_select(approval_task):
        submitted_task = planning_tasks.alias("submitted_task")
        submitter = platform_users.alias("submitter")
        return (
            select(
                planning_approvals,
                planning_cycles.c.name.label("cycle_name"),
                submitted_task.c.title.label("submitted_task_title"),
                approval_task.c.title.label("approval_task_title"),
                submitted_task.c.entity,
                submitted_task.c.scenario,
                submitted_task.c.period,
                submitter.c.display_name.label("submitted_by_name"),
                *(
                    column.label(f"evidence_{column.name}")
                    for column in planning_task_validations.c
                ),
            )
            .join(planning_cycles, planning_cycles.c.cycle_id == planning_approvals.c.cycle_id)
            .join(submitted_task, submitted_task.c.task_id == planning_approvals.c.submitted_task_id)
            .join(approval_task, approval_task.c.task_id == planning_approvals.c.approval_task_id)
            .join(submitter, submitter.c.user_id == planning_approvals.c.submitted_by_user_id)
            .outerjoin(
                planning_task_validations,
                planning_task_validations.c.validation_id
                == planning_approvals.c.validation_id,
            )
        )

    @staticmethod
    def _notify_assignees(
        connection,
        *,
        assigned_user_id,
        assigned_role_id,
        event_type: str,
        severity: NotificationSeverity,
        title: str,
        message: str,
        action_url: str,
        source_type: str,
        source_id: str,
        created_at: datetime,
    ) -> None:
        if assigned_user_id is not None:
            recipients = [int(assigned_user_id)]
        else:
            recipients = [
                int(value)
                for value in connection.execute(
                    select(platform_user_roles.c.user_id)
                    .join(platform_users, platform_users.c.user_id == platform_user_roles.c.user_id)
                    .where(
                        platform_user_roles.c.role_id == assigned_role_id,
                        platform_users.c.is_active.is_(True),
                    )
                ).scalars().all()
            ]
        if recipients:
            connection.execute(insert(user_notifications), [
                {
                    "recipient_user_id": user_id,
                    "event_type": event_type,
                    "severity": severity.value,
                    "title": title,
                    "message": message,
                    "action_url": action_url,
                    "source_type": source_type,
                    "source_id": source_id,
                    "created_at": created_at,
                }
                for user_id in recipients
            ])

    @staticmethod
    def _approval(row) -> PlanningApproval:
        from app.services.planning_validation_repository import (
            PlanningValidationRepository,
        )

        validation = None
        if row["validation_id"] is not None:
            evidence_row = {
                column.name: row[f"evidence_{column.name}"]
                for column in planning_task_validations.c
            }
            validation = PlanningValidationRepository._evidence(evidence_row)
        return PlanningApproval(
            approval_id=int(row["approval_id"]),
            cycle_id=int(row["cycle_id"]),
            cycle_name=str(row["cycle_name"]),
            submitted_task_id=int(row["submitted_task_id"]),
            submitted_task_title=str(row["submitted_task_title"]),
            approval_task_id=int(row["approval_task_id"]),
            approval_task_title=str(row["approval_task_title"]),
            entity=row["entity"],
            scenario=row["scenario"],
            period=row["period"],
            status=PlanningApprovalStatus(str(row["status"])),
            submitted_by_user_id=int(row["submitted_by_user_id"]),
            submitted_by_name=str(row["submitted_by_name"]),
            submitted_at=utc_datetime(row["submitted_at"]),
            decided_by_user_id=int(row["decided_by_user_id"]) if row["decided_by_user_id"] is not None else None,
            decided_at=utc_datetime(row["decided_at"]),
            decision_comment=row["decision_comment"],
            validation=validation,
        )

    @staticmethod
    def _notification(row) -> UserNotification:
        return UserNotification(
            notification_id=int(row["notification_id"]),
            recipient_user_id=int(row["recipient_user_id"]),
            event_type=str(row["event_type"]),
            severity=NotificationSeverity(str(row["severity"])),
            title=str(row["title"]),
            message=str(row["message"]),
            action_url=row["action_url"],
            source_type=row["source_type"],
            source_id=row["source_id"],
            created_at=utc_datetime(row["created_at"]),
            read_at=utc_datetime(row["read_at"]),
        )
