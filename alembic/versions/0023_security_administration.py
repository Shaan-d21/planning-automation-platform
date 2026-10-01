"""Add persistent sessions for security administration.

Revision ID: 0023_security_administration
Revises: 0022_agent_execution_followups
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0023_security_administration"
down_revision: str | None = "0022_agent_execution_followups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_sessions",
        sa.Column("session_id_hash", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("authentication_method", sa.String(length=32), nullable=False),
        sa.Column("login_ip", sa.String(length=45)),
        sa.Column("current_ip", sa.String(length=45)),
        sa.Column("country_code", sa.String(length=2)),
        sa.Column("user_agent", sa.String(length=512)),
        sa.Column("cloudflare_ray", sa.String(length=80)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by_user_id", sa.BigInteger()),
        sa.Column("revoke_reason", sa.String(length=255)),
        sa.ForeignKeyConstraint(["user_id"], ["platform_users.user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["platform_users.user_id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_platform_sessions_user_id", "platform_sessions", ["user_id"])
    op.create_index(
        "ix_platform_sessions_last_seen_at",
        "platform_sessions",
        ["last_seen_at"],
    )
    op.create_index("ix_platform_sessions_current_ip", "platform_sessions", ["current_ip"])


def downgrade() -> None:
    op.drop_index("ix_platform_sessions_current_ip", table_name="platform_sessions")
    op.drop_index("ix_platform_sessions_last_seen_at", table_name="platform_sessions")
    op.drop_index("ix_platform_sessions_user_id", table_name="platform_sessions")
    op.drop_table("platform_sessions")
