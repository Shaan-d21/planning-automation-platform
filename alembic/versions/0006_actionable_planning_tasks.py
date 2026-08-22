"""Retain Oracle execution attempts linked to Planning tasks.

Revision ID: 0006_actionable_tasks
Revises: 0005_four_role_access_model
Create Date: 2026-08-08
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0006_actionable_tasks"
down_revision: str | None = "0005_four_role_access_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planning_task_executions",
        sa.Column("task_execution_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("execution_id", sa.String(64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("initiated_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.CheckConstraint("attempt_number > 0", name="ck_planning_task_executions_attempt_positive"),
        sa.ForeignKeyConstraint(["task_id"], ["planning_tasks.task_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["initiated_by_user_id"], ["platform_users.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("task_execution_id", name="pk_planning_task_executions"),
        sa.UniqueConstraint("execution_id", name="uq_planning_task_executions_execution_id"),
    )
    op.create_index(
        "uq_planning_task_executions_attempt",
        "planning_task_executions",
        ["task_id", "attempt_number"],
        unique=True,
    )
    op.create_index(
        "ix_planning_task_executions_task_linked",
        "planning_task_executions",
        ["task_id", sa.text("linked_at DESC")],
    )


def downgrade() -> None:
    op.drop_table("planning_task_executions")
