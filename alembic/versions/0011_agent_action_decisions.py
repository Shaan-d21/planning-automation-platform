"""Add durable, idempotent AI-agent approval decisions.

Revision ID: 0011_agent_action_decisions
Revises: 0010_unified_oracle_catalog
Create Date: 2026-08-18
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0011_agent_action_decisions"
down_revision: str | None = "0010_unified_oracle_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_action_decisions",
        sa.Column("decision_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("request_id", sa.String(200), nullable=False),
        sa.Column("conversation_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_username", sa.String(80), nullable=False),
        sa.Column("operation_code", sa.String(160), nullable=False),
        sa.Column("artifact_name", sa.String(240)),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("payload_checksum", sa.String(64), nullable=False),
        sa.Column(
            "payload_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("outcome_status", sa.String(40), nullable=False),
        sa.Column("execution_id", sa.String(64)),
        sa.Column("failure_summary", sa.Text()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "decision IN ('APPROVE', 'REJECT')",
            name="ck_agent_action_decisions_decision",
        ),
        sa.CheckConstraint(
            "outcome_status IN ('PROCESSING', 'SUBMITTED', 'APPROVED', "
            "'REJECTED', 'FAILED')",
            name="ck_agent_action_decisions_outcome",
        ),
        sa.PrimaryKeyConstraint(
            "decision_id", name="pk_agent_action_decisions"
        ),
        sa.UniqueConstraint(
            "request_id", name="uq_agent_action_decisions_request_id"
        ),
    )
    op.create_index(
        "ix_agent_action_decisions_conversation",
        "agent_action_decisions",
        ["conversation_id", sa.text("decided_at DESC")],
    )
    op.create_index(
        "ix_agent_action_decisions_execution",
        "agent_action_decisions",
        ["execution_id"],
    )


def downgrade() -> None:
    op.drop_table("agent_action_decisions")
