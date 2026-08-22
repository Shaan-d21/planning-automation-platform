"""Add summary-only Planning validation evidence.

Revision ID: 0007_validation_evidence
Revises: 0006_actionable_tasks
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0007_validation_evidence"
down_revision: str | None = "0006_actionable_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planning_task_validations",
        sa.Column("validation_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("validation_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source_cube", sa.String(128), nullable=False),
        sa.Column("target_cube", sa.String(128)),
        sa.Column("selection", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("checked_cells", sa.Integer(), nullable=False),
        sa.Column("matched_cells", sa.Integer()),
        sa.Column("exception_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("performed_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("warning_acknowledged_by_user_id", sa.BigInteger()),
        sa.Column("warning_acknowledged_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("checked_cells >= 0", name="ck_planning_task_validations_checked_cells_nonnegative"),
        sa.CheckConstraint("exception_count >= 0", name="ck_planning_task_validations_exception_count_nonnegative"),
        sa.CheckConstraint("warning_count >= 0", name="ck_planning_task_validations_warning_count_nonnegative"),
        sa.ForeignKeyConstraint(["task_id"], ["planning_tasks.task_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["performed_by_user_id"], ["platform_users.user_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["warning_acknowledged_by_user_id"], ["platform_users.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("validation_id", name="pk_planning_task_validations"),
    )
    op.create_index(
        "ix_planning_task_validations_task_performed",
        "planning_task_validations",
        ["task_id", sa.text("performed_at DESC")],
    )
    op.add_column("planning_approvals", sa.Column("validation_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_planning_approvals_validation_id_planning_task_validations",
        "planning_approvals",
        "planning_task_validations",
        ["validation_id"],
        ["validation_id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_planning_approvals_validation_id_planning_task_validations",
        "planning_approvals",
        type_="foreignkey",
    )
    op.drop_column("planning_approvals", "validation_id")
    op.drop_table("planning_task_validations")
