"""Scope Planning work and retire the superseded process scheduler.

Revision ID: 0030_planning_work_app_scope
Revises: 0029_schedule_app_scope
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = "0030_planning_work_app_scope"
down_revision: str | None = "0029_schedule_app_scope"
branch_labels = None
depends_on = None


_SELECTED_APPLICATION = """
    SELECT applications.application_id
    FROM oracle_applications AS applications
    JOIN oracle_environment_settings AS settings
      ON settings.base_url = applications.environment_base_url
     AND settings.selected_application = applications.application_name
    WHERE applications.is_active IS TRUE
    ORDER BY settings.selected_at DESC NULLS LAST,
             applications.application_id DESC
    LIMIT 1
"""


def upgrade() -> None:
    # The generic automation_schedules service replaced this disconnected
    # first-generation subsystem. It was no longer reachable from web/API.
    op.drop_table("process_schedules")

    op.drop_constraint(
        "fk_planning_cycles_process_code_planning_processes",
        "planning_cycles",
        type_="foreignkey",
    )
    op.add_column(
        "planning_cycles",
        sa.Column("application_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_planning_cycles_application",
        "planning_cycles",
        "oracle_applications",
        ["application_id"],
        ["application_id"],
        ondelete="RESTRICT",
    )
    op.execute(
        sa.text(
            f"""
            UPDATE planning_cycles
            SET application_id = ({_SELECTED_APPLICATION})
            WHERE application_id IS NULL
            """
        )
    )
    op.drop_index(
        "uq_planning_cycles_active_code",
        table_name="planning_cycles",
    )
    op.drop_index(
        "ix_planning_cycles_status_due",
        table_name="planning_cycles",
    )
    op.create_index(
        "uq_planning_cycles_active_code",
        "planning_cycles",
        ["application_id", sa.text("lower(code)")],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_index(
        "ix_planning_cycles_application_status_due",
        "planning_cycles",
        ["application_id", "status", "due_date"],
    )

    op.add_column(
        "user_notifications",
        sa.Column("application_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_user_notifications_application",
        "user_notifications",
        "oracle_applications",
        ["application_id"],
        ["application_id"],
        ondelete="RESTRICT",
    )
    op.execute(
        sa.text(
            f"""
            UPDATE user_notifications
            SET application_id = ({_SELECTED_APPLICATION})
            WHERE application_id IS NULL
            """
        )
    )
    op.drop_index(
        "ix_user_notifications_recipient_created",
        table_name="user_notifications",
    )
    op.drop_index(
        "ix_user_notifications_recipient_unread",
        table_name="user_notifications",
    )
    op.create_index(
        "ix_user_notifications_application_recipient_created",
        "user_notifications",
        ["application_id", "recipient_user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_user_notifications_application_recipient_unread",
        "user_notifications",
        ["application_id", "recipient_user_id", "read_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_notifications_application_recipient_unread",
        table_name="user_notifications",
    )
    op.drop_index(
        "ix_user_notifications_application_recipient_created",
        table_name="user_notifications",
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
    op.drop_constraint(
        "fk_user_notifications_application",
        "user_notifications",
        type_="foreignkey",
    )
    op.drop_column("user_notifications", "application_id")

    op.drop_index(
        "ix_planning_cycles_application_status_due",
        table_name="planning_cycles",
    )
    op.drop_index(
        "uq_planning_cycles_active_code",
        table_name="planning_cycles",
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
    op.drop_constraint(
        "fk_planning_cycles_application",
        "planning_cycles",
        type_="foreignkey",
    )
    op.drop_column("planning_cycles", "application_id")
    op.create_foreign_key(
        "fk_planning_cycles_process_code_planning_processes",
        "planning_cycles",
        "planning_processes",
        ["process_code"],
        ["process_code"],
        ondelete="SET NULL",
    )

    op.create_table(
        "process_schedules",
        sa.Column("schedule_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("process_code", sa.String(128), nullable=False),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("first_run_local", sa.DateTime(), nullable=False),
        sa.Column("context_mode", sa.String(30), nullable=False),
        sa.Column("run_profile_id", sa.BigInteger()),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True)),
        sa.Column("last_execution_id", sa.String(64)),
        sa.Column("last_outcome", sa.String(20), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["process_code"],
            ["planning_processes.process_code"],
            name="fk_process_schedules_process",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_profile_id"],
            ["process_run_profiles.profile_id"],
            name="fk_process_schedules_profile",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("schedule_id", name="pk_process_schedules"),
    )
    op.create_index(
        "uq_process_schedules_active_name",
        "process_schedules",
        [sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_index(
        "ix_process_schedules_due",
        "process_schedules",
        ["next_run_at"],
        postgresql_where=sa.text(
            "is_enabled IS TRUE AND archived_at IS NULL"
        ),
    )
