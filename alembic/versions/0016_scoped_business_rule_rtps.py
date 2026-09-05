"""Store scoped Calculation Manager runtime-prompt metadata.

Revision ID: 0016_scoped_rule_rtps
Revises: 0015_business_rule_rtp
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0016_scoped_rule_rtps"
down_revision: str | None = "0015_business_rule_rtp"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "business_rule_rtp_parameters",
        sa.Column("scope_type", sa.String(length=20), server_default="UNKNOWN", nullable=False),
    )
    op.add_column(
        "business_rule_rtp_parameters",
        sa.Column("scope_name", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "business_rule_rtp_parameters",
        sa.Column("source_variable_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "business_rule_rtp_parameters",
        sa.Column("limit_type", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "business_rule_rtp_parameters",
        sa.Column("limit_value", sa.Text(), nullable=True),
    )
    op.add_column(
        "business_rule_rtp_parameters",
        sa.Column(
            "source_metadata",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("business_rule_rtp_parameters", "source_metadata")
    op.drop_column("business_rule_rtp_parameters", "limit_value")
    op.drop_column("business_rule_rtp_parameters", "limit_type")
    op.drop_column("business_rule_rtp_parameters", "source_variable_id")
    op.drop_column("business_rule_rtp_parameters", "scope_name")
    op.drop_column("business_rule_rtp_parameters", "scope_type")
