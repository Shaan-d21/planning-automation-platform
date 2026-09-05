"""Add generic platform-owned automation schedules.

Revision ID: 0017_automation_schedules
Revises: 0016_scoped_rule_rtps
Create Date: 2026-08-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0017_automation_schedules"
down_revision: str | None = "0016_scoped_rule_rtps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSON_DOCUMENT = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)


def upgrade() -> None:
    op.create_table(
        "automation_schedules",
        sa.Column("schedule_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("environment_key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_key", sa.String(300), nullable=False),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("first_run_local", sa.DateTime(timezone=False), nullable=False),
        sa.Column("input_policy", sa.String(24), nullable=False),
        sa.Column("configuration", JSON_DOCUMENT, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("concurrency_policy", sa.String(24), nullable=False),
        sa.Column("misfire_policy", sa.String(20), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True)),
        sa.Column("last_execution_id", sa.String(64)),
        sa.Column("last_outcome", sa.String(20), server_default="NEVER", nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "target_type IN ('ORACLE_PIPELINE', 'RTP_REGISTRY_SYNC')",
            name="ck_automation_schedules_target_type",
        ),
        sa.CheckConstraint(
            "frequency IN ('ONE_TIME', 'DAILY', 'WEEKLY', 'MONTHLY')",
            name="ck_automation_schedules_frequency",
        ),
        sa.CheckConstraint(
            "input_policy IN ('ORACLE_DEFAULTS', 'FIXED', 'DYNAMIC')",
            name="ck_automation_schedules_input_policy",
        ),
        sa.CheckConstraint(
            "concurrency_policy IN ('SKIP_IF_ACTIVE')",
            name="ck_automation_schedules_concurrency_policy",
        ),
        sa.CheckConstraint(
            "misfire_policy IN ('RUN_ONCE', 'SKIP')",
            name="ck_automation_schedules_misfire_policy",
        ),
        sa.CheckConstraint(
            "last_outcome IN ('NEVER', 'CLAIMED', 'SUBMITTED', 'FAILED', 'SKIPPED')",
            name="ck_automation_schedules_last_outcome",
        ),
        sa.PrimaryKeyConstraint("schedule_id", name="pk_automation_schedules"),
    )
    op.create_index(
        "uq_automation_schedules_environment_name",
        "automation_schedules",
        ["environment_key", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_index(
        "ix_automation_schedules_due",
        "automation_schedules",
        ["next_run_at"],
        postgresql_where=sa.text("is_enabled = true AND archived_at IS NULL"),
    )
    op.create_index(
        "ix_automation_schedules_environment_target",
        "automation_schedules",
        ["environment_key", "target_type", "target_key"],
    )

    op.create_table(
        "automation_schedule_runs",
        sa.Column("run_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("schedule_id", sa.BigInteger(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("execution_id", sa.String(64)),
        sa.Column("resolved_payload", JSON_DOCUMENT, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.CheckConstraint(
            "status IN ('CLAIMED', 'SUBMITTED', 'FAILED', 'SKIPPED')",
            name="ck_automation_schedule_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["automation_schedules.schedule_id"],
            name="fk_automation_schedule_runs_schedule_id_automation_schedules",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_automation_schedule_runs"),
        sa.UniqueConstraint(
            "schedule_id",
            "scheduled_for",
            name="uq_automation_schedule_runs_occurrence",
        ),
    )
    op.create_index(
        "ix_automation_schedule_runs_schedule_time",
        "automation_schedule_runs",
        ["schedule_id", sa.text("scheduled_for DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_automation_schedule_runs_schedule_time",
        table_name="automation_schedule_runs",
    )
    op.drop_table("automation_schedule_runs")
    op.drop_index(
        "ix_automation_schedules_environment_target",
        table_name="automation_schedules",
    )
    op.drop_index(
        "ix_automation_schedules_due",
        table_name="automation_schedules",
    )
    op.drop_index(
        "uq_automation_schedules_environment_name",
        table_name="automation_schedules",
    )
    op.drop_table("automation_schedules")
