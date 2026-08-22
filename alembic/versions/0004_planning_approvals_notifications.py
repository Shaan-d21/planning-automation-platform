"""Add governed Planning approvals and per-user notifications.

Revision ID: 0004_approvals_notifications
Revises: 0003_external_api_tokens
Create Date: 2026-08-08
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004_approvals_notifications"
down_revision: str | None = "0003_external_api_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planning_approvals",
        sa.Column("approval_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=False),
        sa.Column("submitted_task_id", sa.BigInteger(), nullable=False),
        sa.Column("approval_task_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("submitted_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by_user_id", sa.BigInteger()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_comment", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cycle_id"], ["planning_cycles.cycle_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["submitted_task_id"], ["planning_tasks.task_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approval_task_id"], ["planning_tasks.task_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["submitted_by_user_id"], ["platform_users.user_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["platform_users.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("approval_id", name="pk_planning_approvals"),
    )
    op.create_index(
        "uq_planning_approvals_pending_task",
        "planning_approvals",
        ["approval_task_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "ix_planning_approvals_cycle_status",
        "planning_approvals",
        ["cycle_id", "status"],
    )

    op.create_table(
        "user_notifications",
        sa.Column("notification_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("recipient_user_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("action_url", sa.String(500)),
        sa.Column("source_type", sa.String(60)),
        sa.Column("source_id", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["platform_users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("notification_id", name="pk_user_notifications"),
    )
    op.create_index(
        "ix_user_notifications_recipient_created",
        "user_notifications",
        ["recipient_user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_user_notifications_recipient_unread",
        "user_notifications",
        ["recipient_user_id", "read_at"],
    )


def downgrade() -> None:
    op.drop_table("user_notifications")
    op.drop_table("planning_approvals")
