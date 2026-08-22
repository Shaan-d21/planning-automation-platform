"""Add the durable execution queue.

Revision ID: 0009_durable_execution_queue
Revises: 0008_artifact_registry
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0009_durable_execution_queue"
down_revision: str | None = "0008_artifact_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_queue",
        sa.Column("execution_id", sa.String(64), nullable=False),
        sa.Column("job_type", sa.String(20), nullable=False),
        sa.Column("target_key", sa.String(300), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.String(160)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.CheckConstraint(
            "job_type IN ('OPERATION', 'PROCESS')",
            name="ck_execution_queue_job_type",
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCESS', 'FAILED', "
            "'RECOVERY_REQUIRED')",
            name="ck_execution_queue_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_execution_queue_attempt_nonnegative",
        ),
        sa.PrimaryKeyConstraint(
            "execution_id",
            name="pk_execution_queue",
        ),
    )
    op.create_index(
        "ix_execution_queue_claim",
        "execution_queue",
        ["status", "available_at", "priority", "created_at"],
    )
    op.create_index(
        "ix_execution_queue_lease",
        "execution_queue",
        ["lease_expires_at"],
        postgresql_where=sa.text("status = 'RUNNING'"),
    )
    op.create_index(
        "uq_execution_queue_active_target",
        "execution_queue",
        ["job_type", sa.text("lower(target_key)")],
        unique=True,
        postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_execution_queue_active_target",
        table_name="execution_queue",
    )
    op.drop_index("ix_execution_queue_lease", table_name="execution_queue")
    op.drop_index("ix_execution_queue_claim", table_name="execution_queue")
    op.drop_table("execution_queue")
