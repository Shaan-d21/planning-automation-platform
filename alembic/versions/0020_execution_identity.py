"""Record the effective Oracle identity for durable executions.

Revision ID: 0020_execution_identity
Revises: 0019_environment_selection
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0020_execution_identity"
down_revision: str | None = "0019_environment_selection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column("oracle_execution_username", sa.String(length=254)),
    )


def downgrade() -> None:
    op.drop_column("workflow_runs", "oracle_execution_username")
