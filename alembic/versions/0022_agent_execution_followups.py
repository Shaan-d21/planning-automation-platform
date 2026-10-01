"""Persist idempotent agent execution completion follow-ups.

Revision ID: 0022_agent_execution_followups
Revises: 0021_execution_cancellation
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0022_agent_execution_followups"
down_revision: str | None = "0021_execution_cancellation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_action_decisions",
        sa.Column("completion_status", sa.String(length=30)),
    )
    op.add_column(
        "agent_action_decisions",
        sa.Column("completion_message_id", sa.BigInteger()),
    )
    op.add_column(
        "agent_action_decisions",
        sa.Column("completion_notified_at", sa.DateTime(timezone=True)),
    )
    with op.batch_alter_table("agent_action_decisions") as batch_op:
        batch_op.create_check_constraint(
            "ck_agent_action_decisions_completion_status",
            "completion_status IS NULL OR completion_status IN "
            "('SUCCESS', 'FAILED', 'RECOVERY_REQUIRED', 'CANCELLED')",
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_action_decisions") as batch_op:
        batch_op.drop_constraint(
            "ck_agent_action_decisions_completion_status",
            type_="check",
        )
    op.drop_column("agent_action_decisions", "completion_notified_at")
    op.drop_column("agent_action_decisions", "completion_message_id")
    op.drop_column("agent_action_decisions", "completion_status")
