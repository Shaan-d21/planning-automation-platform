"""Add operational Planning cycles and actionable task dependencies.

Revision ID: 0002_cycle_task_engine
Revises: 0001_platform_schema
Create Date: 2026-08-07
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002_cycle_task_engine"
down_revision: str | None = "0001_platform_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planning_cycles",
        sa.Column("cycle_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("cycle_type", sa.String(40), nullable=False),
        sa.Column("process_code", sa.String(128)),
        sa.Column("scenario", sa.String(120)),
        sa.Column("year", sa.String(40), nullable=False),
        sa.Column("actual_through_period", sa.String(80)),
        sa.Column("forecast_start_period", sa.String(80)),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "due_date >= start_date",
            name="ck_planning_cycles_date_order",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["platform_users.user_id"],
            name="fk_planning_cycles_created_by_user_id_platform_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["process_code"],
            ["planning_processes.process_code"],
            name="fk_planning_cycles_process_code_planning_processes",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("cycle_id", name="pk_planning_cycles"),
    )
    op.create_index(
        "uq_planning_cycles_active_code",
        "planning_cycles",
        [sa.text("lower(code)")],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_index(
        "ix_planning_cycles_status_due",
        "planning_cycles",
        ["status", "due_date"],
    )

    op.create_table(
        "planning_cycle_stages",
        sa.Column("stage_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("start_date", sa.Date()),
        sa.Column("due_date", sa.Date()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "due_date IS NULL OR start_date IS NULL OR due_date >= start_date",
            name="ck_planning_cycle_stages_date_order",
        ),
        sa.CheckConstraint(
            "sequence > 0",
            name="ck_planning_cycle_stages_sequence_positive",
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"],
            ["planning_cycles.cycle_id"],
            name="fk_planning_cycle_stages_cycle_id_planning_cycles",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("stage_id", name="pk_planning_cycle_stages"),
    )
    op.create_index(
        "uq_planning_cycle_stages_code_ci",
        "planning_cycle_stages",
        ["cycle_id", sa.text("lower(code)")],
        unique=True,
    )
    op.create_index(
        "uq_planning_cycle_stages_sequence",
        "planning_cycle_stages",
        ["cycle_id", "sequence"],
        unique=True,
    )

    op.create_table(
        "planning_tasks",
        sa.Column("task_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("stage_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("task_type", sa.String(60), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("assigned_user_id", sa.BigInteger()),
        sa.Column("assigned_role_id", sa.BigInteger()),
        sa.Column("entity", sa.String(160)),
        sa.Column("scenario", sa.String(120)),
        sa.Column("period", sa.String(80)),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("action_type", sa.String(60), nullable=False),
        sa.Column(
            "action_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "(assigned_user_id IS NOT NULL AND assigned_role_id IS NULL) OR "
            "(assigned_user_id IS NULL AND assigned_role_id IS NOT NULL)",
            name="ck_planning_tasks_one_assignee",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_role_id"],
            ["platform_roles.role_id"],
            name="fk_planning_tasks_assigned_role_id_platform_roles",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_user_id"],
            ["platform_users.user_id"],
            name="fk_planning_tasks_assigned_user_id_platform_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["platform_users.user_id"],
            name="fk_planning_tasks_created_by_user_id_platform_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"],
            ["planning_cycle_stages.stage_id"],
            name="fk_planning_tasks_stage_id_planning_cycle_stages",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id", name="pk_planning_tasks"),
    )
    op.create_index(
        "ix_planning_tasks_user_status_due",
        "planning_tasks",
        ["assigned_user_id", "status", "due_at"],
    )
    op.create_index(
        "ix_planning_tasks_role_status_due",
        "planning_tasks",
        ["assigned_role_id", "status", "due_at"],
    )
    op.create_index(
        "ix_planning_tasks_stage",
        "planning_tasks",
        ["stage_id"],
    )

    op.create_table(
        "planning_task_dependencies",
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("depends_on_task_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "task_id <> depends_on_task_id",
            name="ck_planning_task_dependencies_not_self",
        ),
        sa.ForeignKeyConstraint(
            ["depends_on_task_id"],
            ["planning_tasks.task_id"],
            name=(
                "fk_planning_task_dependencies_depends_on_task_id_"
                "planning_tasks"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["planning_tasks.task_id"],
            name="fk_planning_task_dependencies_task_id_planning_tasks",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "task_id",
            "depends_on_task_id",
            name="pk_planning_task_dependencies",
        ),
    )


def downgrade() -> None:
    op.drop_table("planning_task_dependencies")
    op.drop_table("planning_tasks")
    op.drop_table("planning_cycle_stages")
    op.drop_table("planning_cycles")
