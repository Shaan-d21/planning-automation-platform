"""Add normalized Oracle Task Manager synchronization storage.

Revision ID: 0027_task_manager_sync
Revises: 0026_application_workspaces
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0027_task_manager_sync"
down_revision: str | None = "0026_application_workspaces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_document = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "task_manager_sources",
        sa.Column("application_id", sa.BigInteger(), nullable=False),
        sa.Column("report_group", sa.String(length=200), nullable=False),
        sa.Column("report_name", sa.String(length=200), nullable=False),
        sa.Column(
            "parameters",
            json_document,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("updated_by_user_id", sa.BigInteger()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("last_sync_status", sa.String(length=20)),
        sa.Column(
            "last_sync_record_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_error", sa.Text()),
        sa.CheckConstraint(
            "last_sync_status IS NULL OR last_sync_status IN "
            "('SUCCESS', 'FAILED')",
            name="ck_task_manager_sources_sync_status",
        ),
        sa.CheckConstraint(
            "last_sync_record_count >= 0",
            name="ck_task_manager_sources_record_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["oracle_applications.application_id"],
            name="fk_task_manager_sources_application_id_oracle_applications",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["platform_users.user_id"],
            name="fk_task_manager_source_updated_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "application_id",
            name="pk_task_manager_sources",
        ),
    )
    op.create_table(
        "task_manager_tasks",
        sa.Column(
            "task_manager_task_id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("application_id", sa.BigInteger(), nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=200)),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("schedule_name", sa.String(length=300)),
        sa.Column("period_name", sa.String(length=200)),
        sa.Column("status", sa.String(length=120)),
        sa.Column("owner", sa.String(length=300)),
        sa.Column("assignee", sa.String(length=300)),
        sa.Column("approver", sa.String(length=300)),
        sa.Column("organization", sa.String(length=300)),
        sa.Column("task_type", sa.String(length=160)),
        sa.Column("priority", sa.String(length=80)),
        sa.Column("description", sa.Text()),
        sa.Column("parent_task", sa.String(length=500)),
        sa.Column("dependency", sa.Text()),
        sa.Column("start_at", sa.DateTime(timezone=True)),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "attributes",
            json_document,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("synchronized_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["oracle_applications.application_id"],
            name="fk_task_manager_tasks_application_id_oracle_applications",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "task_manager_task_id",
            name="pk_task_manager_tasks",
        ),
        sa.UniqueConstraint(
            "application_id",
            "source_key",
            name="uq_task_manager_tasks_application_source_key",
        ),
    )
    op.create_index(
        "ix_task_manager_tasks_application_schedule",
        "task_manager_tasks",
        ["application_id", "schedule_name"],
    )
    op.create_index(
        "ix_task_manager_tasks_application_status",
        "task_manager_tasks",
        ["application_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_manager_tasks_application_status",
        table_name="task_manager_tasks",
    )
    op.drop_index(
        "ix_task_manager_tasks_application_schedule",
        table_name="task_manager_tasks",
    )
    op.drop_table("task_manager_tasks")
    op.drop_table("task_manager_sources")

