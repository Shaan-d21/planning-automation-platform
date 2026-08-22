"""Expand the environment catalog to every live-discovered Oracle artifact.

Revision ID: 0010_unified_oracle_catalog
Revises: 0009_durable_execution_queue
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0010_unified_oracle_catalog"
down_revision: str | None = "0009_durable_execution_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_EXPANDED_TYPES = (
    "artifact_type IN ('PIPELINE', 'DATA_INTEGRATION', "
    "'BUSINESS_RULE', 'DATA_MAP', 'METADATA_IMPORT_JOB', "
    "'DATA_IMPORT_JOB', 'CUBE_REFRESH_JOB', 'CUBE')"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_oracle_artifacts_artifact_type",
        "oracle_artifacts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_oracle_artifacts_artifact_type",
        "oracle_artifacts",
        _EXPANDED_TYPES,
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM oracle_artifacts WHERE artifact_type NOT IN "
        "('PIPELINE', 'DATA_INTEGRATION')"
    )
    op.drop_constraint(
        "ck_oracle_artifacts_artifact_type",
        "oracle_artifacts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_oracle_artifacts_artifact_type",
        "oracle_artifacts",
        "artifact_type IN ('PIPELINE', 'DATA_INTEGRATION')",
    )
