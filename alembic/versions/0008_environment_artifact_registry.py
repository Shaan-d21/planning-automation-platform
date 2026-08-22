"""Replace the Pipeline-only registry with an environment artifact catalog.

Revision ID: 0008_artifact_registry
Revises: 0007_validation_evidence
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0008_artifact_registry"
down_revision: str | None = "0007_validation_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oracle_artifacts",
        sa.Column("artifact_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("environment_key", sa.String(64), nullable=False),
        sa.Column("environment_base_url", sa.String(500), nullable=False),
        sa.Column("application_name", sa.String(128), nullable=False),
        sa.Column("artifact_type", sa.String(32), nullable=False),
        sa.Column("oracle_identifier", sa.String(250), nullable=False),
        sa.Column("normalized_identifier", sa.String(250), nullable=False),
        sa.Column("display_name", sa.String(250), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("verification_status", sa.String(32), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("consecutive_missing_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "artifact_type IN ('PIPELINE', 'DATA_INTEGRATION')",
            name="ck_oracle_artifacts_artifact_type",
        ),
        sa.CheckConstraint(
            "verification_status IN "
            "('VERIFIED', 'PENDING', 'MISSING', 'UNAVAILABLE', 'INACTIVE')",
            name="ck_oracle_artifacts_verification_status",
        ),
        sa.CheckConstraint(
            "consecutive_missing_count >= 0",
            name="ck_oracle_artifacts_missing_count_nonnegative",
        ),
        sa.PrimaryKeyConstraint("artifact_id", name="pk_oracle_artifacts"),
        sa.UniqueConstraint(
            "environment_key",
            "artifact_type",
            "normalized_identifier",
            name="uq_oracle_artifacts_environment_type_identifier",
        ),
    )
    op.create_index(
        "ix_oracle_artifacts_environment_type_status",
        "oracle_artifacts",
        ["environment_key", "artifact_type", "verification_status"],
    )
    op.execute(
        sa.text(
            """
            INSERT INTO oracle_artifacts (
                environment_key,
                environment_base_url,
                application_name,
                artifact_type,
                oracle_identifier,
                normalized_identifier,
                display_name,
                description,
                source,
                verification_status,
                is_active,
                consecutive_missing_count,
                created_at,
                updated_at
            )
            SELECT
                '__legacy__',
                'legacy://unscoped',
                'Legacy',
                'PIPELINE',
                code,
                lower(code),
                name,
                description,
                'LEGACY',
                'PENDING',
                true,
                0,
                created_at,
                updated_at
            FROM oracle_pipelines
            """
        )
    )
    op.drop_index("uq_oracle_pipelines_code_ci", table_name="oracle_pipelines")
    op.drop_table("oracle_pipelines")


def downgrade() -> None:
    op.create_table(
        "oracle_pipelines",
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("code", name="pk_oracle_pipelines"),
    )
    op.create_index(
        "uq_oracle_pipelines_code_ci",
        "oracle_pipelines",
        [sa.text("lower(code)")],
        unique=True,
    )
    op.execute(
        sa.text(
            """
            INSERT INTO oracle_pipelines (
                code, name, description, created_at, updated_at
            )
            SELECT DISTINCT ON (normalized_identifier)
                oracle_identifier,
                display_name,
                description,
                created_at,
                updated_at
            FROM oracle_artifacts
            WHERE artifact_type = 'PIPELINE'
            ORDER BY normalized_identifier, updated_at DESC
            """
        )
    )
    op.drop_index(
        "ix_oracle_artifacts_environment_type_status",
        table_name="oracle_artifacts",
    )
    op.drop_table("oracle_artifacts")
