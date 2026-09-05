"""Persist Oracle environment application discovery and selection.

Revision ID: 0019_environment_selection
Revises: 0018_scheduled_rtp_sync
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0019_environment_selection"
down_revision: str | None = "0018_scheduled_rtp_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oracle_environment_settings",
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("deployment_mode", sa.String(length=32), nullable=False),
        sa.Column("selected_application", sa.String(length=128)),
        sa.Column("selection_source", sa.String(length=32)),
        sa.Column(
            "discovered_applications",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("last_discovered_at", sa.DateTime(timezone=True)),
        sa.Column("last_discovery_error", sa.Text()),
        sa.Column("selected_at", sa.DateTime(timezone=True)),
        sa.Column("selected_by_user_id", sa.BigInteger()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "selection_source IS NULL OR selection_source IN "
            "('DATABASE', 'ENVIRONMENT', 'AUTO_DISCOVERY', "
            "'ADMIN_SELECTION')",
            name="ck_oracle_environment_settings_selection_source",
        ),
        sa.ForeignKeyConstraint(
            ["selected_by_user_id"],
            ["platform_users.user_id"],
            name="fk_oracle_env_selected_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "base_url",
            name="pk_oracle_environment_settings",
        ),
    )


def downgrade() -> None:
    op.drop_table("oracle_environment_settings")
