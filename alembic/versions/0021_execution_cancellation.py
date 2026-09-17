"""Add durable safe-stop requests for queued and running executions.

Revision ID: 0021_execution_cancellation
Revises: 0020_execution_identity
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0021_execution_cancellation"
down_revision: str | None = "0020_execution_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "execution_queue",
        sa.Column("cancellation_requested_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "execution_queue",
        sa.Column("cancellation_requested_by", sa.String(length=80)),
    )
    with op.batch_alter_table("execution_queue") as batch_op:
        batch_op.drop_constraint(
            "ck_execution_queue_status",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_execution_queue_status",
            "status IN ('QUEUED', 'RUNNING', 'SUCCESS', 'FAILED', "
            "'RECOVERY_REQUIRED', 'CANCELLED')",
        )


def downgrade() -> None:
    op.execute(
        "UPDATE execution_queue SET status = 'FAILED', "
        "error_message = COALESCE(error_message, 'Cancelled before downgrade') "
        "WHERE status = 'CANCELLED'"
    )
    with op.batch_alter_table("execution_queue") as batch_op:
        batch_op.drop_constraint(
            "ck_execution_queue_status",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_execution_queue_status",
            "status IN ('QUEUED', 'RUNNING', 'SUCCESS', 'FAILED', "
            "'RECOVERY_REQUIRED')",
        )
    op.drop_column("execution_queue", "cancellation_requested_by")
    op.drop_column("execution_queue", "cancellation_requested_at")
