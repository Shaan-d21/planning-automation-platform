"""Add provider-neutral federated identity persistence.

Revision ID: 0013_federated_identity
Revises: 0012_standalone_flow_queue
Create Date: 2026-08-20
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0013_federated_identity"
down_revision: str | None = "0012_standalone_flow_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "platform_users",
        "password_hash",
        existing_type=sa.Text(),
        nullable=True,
    )

    op.create_table(
        "identity_providers",
        sa.Column("provider_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("provider_type", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("issuer_url", sa.String(500)),
        sa.Column("environment_key", sa.String(64)),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "safe_configuration",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "provider_type IN ('LOCAL', 'ORACLE_CLOUD', 'OIDC', 'LDAP')",
            name="ck_identity_providers_provider_type",
        ),
        sa.PrimaryKeyConstraint("provider_id", name="pk_identity_providers"),
        sa.UniqueConstraint("code", name="uq_identity_providers_code"),
    )
    op.create_index(
        "uq_identity_providers_code_ci",
        "identity_providers",
        [sa.text("lower(code)")],
        unique=True,
    )

    op.create_table(
        "external_identities",
        sa.Column(
            "external_identity_id",
            sa.BigInteger(),
            sa.Identity(),
            nullable=False,
        ),
        sa.Column("provider_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger()),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("username", sa.String(254), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(254)),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["identity_providers.provider_id"],
            ondelete="CASCADE",
            name="fk_external_identities_provider_id_identity_providers",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["platform_users.user_id"],
            ondelete="SET NULL",
            name="fk_external_identities_user_id_platform_users",
        ),
        sa.PrimaryKeyConstraint(
            "external_identity_id",
            name="pk_external_identities",
        ),
        sa.UniqueConstraint(
            "provider_id",
            "subject",
            name="uq_external_identities_provider_subject",
        ),
        sa.UniqueConstraint(
            "provider_id",
            "user_id",
            name="uq_external_identities_provider_user",
        ),
    )
    op.create_index(
        "ix_external_identities_provider_active",
        "external_identities",
        ["provider_id", "is_active"],
    )
    op.create_index(
        "ix_external_identities_user",
        "external_identities",
        ["user_id"],
    )

    op.create_table(
        "external_entitlements",
        sa.Column("entitlement_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("provider_id", sa.BigInteger(), nullable=False),
        sa.Column("entitlement_type", sa.String(32), nullable=False),
        sa.Column("external_key", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "entitlement_type IN ('APPLICATION_ROLE', 'GRANULAR_ROLE', 'GROUP')",
            name="ck_external_entitlements_entitlement_type",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["identity_providers.provider_id"],
            ondelete="CASCADE",
            name="fk_external_entitlements_provider_id_identity_providers",
        ),
        sa.PrimaryKeyConstraint("entitlement_id", name="pk_external_entitlements"),
        sa.UniqueConstraint(
            "provider_id",
            "entitlement_type",
            "external_key",
            name="uq_external_entitlements_provider_type_key",
        ),
    )

    op.create_table(
        "external_identity_entitlements",
        sa.Column("external_identity_id", sa.BigInteger(), nullable=False),
        sa.Column("entitlement_id", sa.BigInteger(), nullable=False),
        sa.Column("assignment_type", sa.String(16), nullable=False),
        sa.Column("granted_through_group", sa.String(255)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "assignment_type IN ('DIRECT', 'INHERITED')",
            name="ck_external_identity_entitlements_assignment_type",
        ),
        sa.ForeignKeyConstraint(
            ["external_identity_id"],
            ["external_identities.external_identity_id"],
            ondelete="CASCADE",
            name="fk_ext_identity_entitlements_identity",
        ),
        sa.ForeignKeyConstraint(
            ["entitlement_id"],
            ["external_entitlements.entitlement_id"],
            ondelete="CASCADE",
            name="fk_ext_identity_entitlements_entitlement",
        ),
        sa.PrimaryKeyConstraint(
            "external_identity_id",
            "entitlement_id",
            name="pk_external_identity_entitlements",
        ),
    )

    op.create_table(
        "identity_role_mappings",
        sa.Column("mapping_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("provider_id", sa.BigInteger(), nullable=False),
        sa.Column("entitlement_id", sa.BigInteger(), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["identity_providers.provider_id"],
            ondelete="CASCADE",
            name="fk_identity_role_mappings_provider_id_identity_providers",
        ),
        sa.ForeignKeyConstraint(
            ["entitlement_id"],
            ["external_entitlements.entitlement_id"],
            ondelete="CASCADE",
            name="fk_identity_role_mappings_entitlement",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["platform_roles.role_id"],
            ondelete="RESTRICT",
            name="fk_identity_role_mappings_role_id_platform_roles",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["platform_users.user_id"],
            ondelete="SET NULL",
            name="fk_identity_role_mappings_created_by",
        ),
        sa.PrimaryKeyConstraint("mapping_id", name="pk_identity_role_mappings"),
        sa.UniqueConstraint(
            "entitlement_id",
            name="uq_identity_role_mappings_entitlement_id",
        ),
        sa.UniqueConstraint(
            "provider_id",
            "entitlement_id",
            name="uq_identity_role_mappings_provider_entitlement",
        ),
    )

    op.create_table(
        "identity_sync_runs",
        sa.Column("sync_run_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("provider_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("identities_seen", sa.Integer(), server_default="0", nullable=False),
        sa.Column("identities_linked", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "identities_deactivated",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("entitlements_seen", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_summary", sa.Text()),
        sa.Column("initiated_by_user_id", sa.BigInteger()),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'FAILED')",
            name="ck_identity_sync_runs_status",
        ),
        sa.CheckConstraint(
            "identities_seen >= 0",
            name="ck_identity_sync_runs_identities_seen_nonnegative",
        ),
        sa.CheckConstraint(
            "identities_linked >= 0",
            name="ck_identity_sync_runs_identities_linked_nonnegative",
        ),
        sa.CheckConstraint(
            "identities_deactivated >= 0",
            name="ck_identity_sync_runs_identities_deactivated_nonnegative",
        ),
        sa.CheckConstraint(
            "entitlements_seen >= 0",
            name="ck_identity_sync_runs_entitlements_seen_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["identity_providers.provider_id"],
            ondelete="CASCADE",
            name="fk_identity_sync_runs_provider_id_identity_providers",
        ),
        sa.ForeignKeyConstraint(
            ["initiated_by_user_id"],
            ["platform_users.user_id"],
            ondelete="SET NULL",
            name="fk_identity_sync_runs_initiated_by",
        ),
        sa.PrimaryKeyConstraint("sync_run_id", name="pk_identity_sync_runs"),
    )
    op.create_index(
        "ix_identity_sync_runs_provider_started",
        "identity_sync_runs",
        ["provider_id", sa.text("started_at DESC")],
    )


def downgrade() -> None:
    op.drop_table("identity_sync_runs")
    op.drop_table("identity_role_mappings")
    op.drop_table("external_identity_entitlements")
    op.drop_table("external_entitlements")
    op.drop_table("external_identities")
    op.drop_table("identity_providers")
    op.execute(
        "UPDATE platform_users SET password_hash = "
        "'disabled-federated-account' WHERE password_hash IS NULL"
    )
    op.alter_column(
        "platform_users",
        "password_hash",
        existing_type=sa.Text(),
        nullable=False,
    )
