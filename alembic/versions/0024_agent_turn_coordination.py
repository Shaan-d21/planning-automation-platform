"""Add durable agent turn coordination and cancellation.

Revision ID: 0024_agent_turn_coordination
Revises: 0023_security_administration
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0024_agent_turn_coordination"
down_revision: str | None = "0023_security_administration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_turns",
        sa.Column("turn_id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("client_message_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("user_message_id", sa.BigInteger()),
        sa.Column("assistant_message_id", sa.BigInteger()),
        sa.Column("error_summary", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'CANCEL_REQUESTED', 'COMPLETED', "
            "'FAILED', 'CANCELLED')",
            name="agent_turn_status",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_conversations.conversation_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["platform_users.user_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["user_message_id"],
            ["agent_messages.message_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"],
            ["agent_messages.message_id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_agent_turn_client_message",
        ),
    )
    op.create_index(
        "ix_agent_turns_conversation_created",
        "agent_turns",
        ["conversation_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "uq_agent_turns_active_conversation",
        "agent_turns",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('RUNNING', 'CANCEL_REQUESTED')"
        ),
        sqlite_where=sa.text(
            "status IN ('RUNNING', 'CANCEL_REQUESTED')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_agent_turns_active_conversation", table_name="agent_turns"
    )
    op.drop_index(
        "ix_agent_turns_conversation_created", table_name="agent_turns"
    )
    op.drop_table("agent_turns")
