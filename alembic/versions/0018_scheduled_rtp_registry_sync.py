"""Support completed synchronous schedule occurrences.

Revision ID: 0018_scheduled_rtp_sync
Revises: 0017_automation_schedules
Create Date: 2026-08-25
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0018_scheduled_rtp_sync"
down_revision: str | None = "0017_automation_schedules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("automation_schedules") as batch_op:
        batch_op.drop_constraint(
            "ck_automation_schedules_last_outcome",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_automation_schedules_last_outcome",
            "last_outcome IN ('NEVER', 'CLAIMED', 'SUBMITTED', "
            "'COMPLETED', 'FAILED', 'SKIPPED')",
        )
    with op.batch_alter_table("automation_schedule_runs") as batch_op:
        batch_op.drop_constraint(
            "ck_automation_schedule_runs_status",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_automation_schedule_runs_status",
            "status IN ('CLAIMED', 'SUBMITTED', 'COMPLETED', "
            "'FAILED', 'SKIPPED')",
        )


def downgrade() -> None:
    op.execute(
        "UPDATE automation_schedule_runs SET status = 'SUBMITTED' "
        "WHERE status = 'COMPLETED'"
    )
    op.execute(
        "UPDATE automation_schedules SET last_outcome = 'SUBMITTED' "
        "WHERE last_outcome = 'COMPLETED'"
    )
    with op.batch_alter_table("automation_schedule_runs") as batch_op:
        batch_op.drop_constraint(
            "ck_automation_schedule_runs_status",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_automation_schedule_runs_status",
            "status IN ('CLAIMED', 'SUBMITTED', 'FAILED', 'SKIPPED')",
        )
    with op.batch_alter_table("automation_schedules") as batch_op:
        batch_op.drop_constraint(
            "ck_automation_schedules_last_outcome",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_automation_schedules_last_outcome",
            "last_outcome IN ('NEVER', 'CLAIMED', 'SUBMITTED', "
            "'FAILED', 'SKIPPED')",
        )
