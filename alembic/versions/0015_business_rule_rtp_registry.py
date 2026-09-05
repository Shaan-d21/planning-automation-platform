"""Add environment-scoped Business Rule RTP registry.

Revision ID: 0015_business_rule_rtp
Revises: 0014_oracle_oidc_sign_in
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0015_business_rule_rtp"
down_revision: str | None = "0014_oracle_oidc_sign_in"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_document = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "business_rule_rtp_sync_runs",
        sa.Column("sync_run_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("environment_key", sa.String(64), nullable=False),
        sa.Column("environment_base_url", sa.String(500), nullable=False),
        sa.Column("application_name", sa.String(128), nullable=False),
        sa.Column("source_name", sa.String(500), nullable=False),
        sa.Column("source_checksum", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("rules_imported", sa.Integer(), server_default="0", nullable=False),
        sa.Column("prompts_imported", sa.Integer(), server_default="0", nullable=False),
        sa.Column("warnings", json_document, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("error_summary", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'COMPLETED', 'FAILED')",
            name="ck_business_rule_rtp_sync_runs_status",
        ),
        sa.CheckConstraint(
            "rules_imported >= 0",
            name="ck_business_rule_rtp_sync_runs_rules_nonnegative",
        ),
        sa.CheckConstraint(
            "prompts_imported >= 0",
            name="ck_business_rule_rtp_sync_runs_prompts_nonnegative",
        ),
        sa.PrimaryKeyConstraint("sync_run_id", name="pk_business_rule_rtp_sync_runs"),
    )
    op.create_index(
        "ix_business_rule_rtp_sync_runs_environment_started",
        "business_rule_rtp_sync_runs",
        ["environment_key", "started_at"],
    )
    op.create_table(
        "business_rule_rtp_definitions",
        sa.Column("definition_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("environment_key", sa.String(64), nullable=False),
        sa.Column("application_name", sa.String(128), nullable=False),
        sa.Column("rule_name", sa.String(250), nullable=False),
        sa.Column("normalized_rule_name", sa.String(250), nullable=False),
        sa.Column("cube_name", sa.String(128)),
        sa.Column("source_name", sa.String(500), nullable=False),
        sa.Column("source_path", sa.String(1000), nullable=False),
        sa.Column("source_checksum", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(32), nullable=False),
        sa.Column("definition_checksum", sa.String(64), nullable=False),
        sa.Column("source_sync_run_id", sa.BigInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("synchronized_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_sync_run_id"],
            ["business_rule_rtp_sync_runs.sync_run_id"],
            name="fk_br_rtp_definition_sync",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("definition_id", name="pk_business_rule_rtp_definitions"),
        sa.UniqueConstraint(
            "environment_key",
            "normalized_rule_name",
            name="uq_business_rule_rtp_definitions_environment_rule",
        ),
    )
    op.create_index(
        "ix_business_rule_rtp_definitions_environment_active",
        "business_rule_rtp_definitions",
        ["environment_key", "is_active"],
    )
    op.create_table(
        "business_rule_rtp_parameters",
        sa.Column("parameter_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("definition_id", sa.BigInteger(), nullable=False),
        sa.Column("prompt_order", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(250), nullable=False),
        sa.Column("normalized_name", sa.String(250), nullable=False),
        sa.Column("label", sa.String(500), nullable=False),
        sa.Column("value_type", sa.String(80), nullable=False),
        sa.Column("dimension", sa.String(128)),
        sa.Column("default_value", sa.Text()),
        sa.Column("has_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("required_at_launch", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_hidden", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("allow_multiple", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("security_mode", sa.String(80)),
        sa.CheckConstraint(
            "prompt_order > 0",
            name="ck_business_rule_rtp_parameters_order_positive",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["business_rule_rtp_definitions.definition_id"],
            name="fk_br_rtp_parameter_definition",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("parameter_id", name="pk_business_rule_rtp_parameters"),
        sa.UniqueConstraint(
            "definition_id",
            "normalized_name",
            name="uq_business_rule_rtp_parameters_definition_name",
        ),
        sa.UniqueConstraint(
            "definition_id",
            "prompt_order",
            name="uq_business_rule_rtp_parameters_definition_order",
        ),
    )
    op.create_index(
        "ix_business_rule_rtp_parameters_definition_order",
        "business_rule_rtp_parameters",
        ["definition_id", "prompt_order"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_business_rule_rtp_parameters_definition_order",
        table_name="business_rule_rtp_parameters",
    )
    op.drop_table("business_rule_rtp_parameters")
    op.drop_index(
        "ix_business_rule_rtp_definitions_environment_active",
        table_name="business_rule_rtp_definitions",
    )
    op.drop_table("business_rule_rtp_definitions")
    op.drop_index(
        "ix_business_rule_rtp_sync_runs_environment_started",
        table_name="business_rule_rtp_sync_runs",
    )
    op.drop_table("business_rule_rtp_sync_runs")
