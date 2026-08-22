"""Add revocable scoped credentials for Excel and external clients.

Revision ID: 0003_external_api_tokens
Revises: 0002_cycle_task_engine
Create Date: 2026-08-07
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0003_external_api_tokens"
down_revision: str | None = "0002_cycle_task_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("token_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("token_prefix", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_ip", sa.String(45)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["platform_users.user_id"],
            name="fk_api_tokens_user_id_platform_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_id", name="pk_api_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_api_tokens_token_hash"),
        sa.UniqueConstraint("token_prefix", name="uq_api_tokens_token_prefix"),
    )
    op.create_index(
        "ix_api_tokens_user_active",
        "api_tokens",
        ["user_id", "revoked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_api_tokens_user_active", table_name="api_tokens")
    op.drop_table("api_tokens")
