"""Persist the verified Oracle EPM business-process context.

Revision ID: 0025_epm_product_context
Revises: 0024_agent_turn_coordination
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0025_epm_product_context"
down_revision: str | None = "0024_agent_turn_coordination"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "oracle_environment_settings",
        sa.Column("selected_business_process", sa.String(length=32)),
    )
    op.create_check_constraint(
        "ck_oracle_environment_settings_selected_business_process",
        "oracle_environment_settings",
        "selected_business_process IS NULL OR "
        "selected_business_process IN ('PLANNING', 'FCCS', 'UNKNOWN')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_oracle_environment_settings_selected_business_process",
        "oracle_environment_settings",
        type_="check",
    )
    op.drop_column(
        "oracle_environment_settings",
        "selected_business_process",
    )
