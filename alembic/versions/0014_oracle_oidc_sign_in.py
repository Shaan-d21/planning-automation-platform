"""Bind validated Oracle OIDC subjects to synchronized identities.

Revision ID: 0014_oracle_oidc_sign_in
Revises: 0013_federated_identity
Create Date: 2026-08-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0014_oracle_oidc_sign_in"
down_revision: str | None = "0013_federated_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "external_identities",
        sa.Column("authenticated_subject", sa.String(255)),
    )
    op.add_column(
        "external_identities",
        sa.Column("last_authenticated_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "uq_external_identities_provider_authenticated_subject",
        "external_identities",
        ["provider_id", "authenticated_subject"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_external_identities_provider_authenticated_subject",
        table_name="external_identities",
    )
    op.drop_column("external_identities", "last_authenticated_at")
    op.drop_column("external_identities", "authenticated_subject")
