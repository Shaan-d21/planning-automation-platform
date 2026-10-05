"""Register Oracle applications and session workspace ownership.

Revision ID: 0026_application_workspaces
Revises: 0025_epm_product_context
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0026_application_workspaces"
down_revision: str | None = "0025_epm_product_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oracle_applications",
        sa.Column(
            "application_id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("environment_base_url", sa.String(length=500), nullable=False),
        sa.Column("application_name", sa.String(length=128), nullable=False),
        sa.Column("product_type", sa.String(length=128)),
        sa.Column("application_type", sa.String(length=128)),
        sa.Column("business_process", sa.String(length=32), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "business_process IN ('PLANNING', 'FCCS', 'UNKNOWN')",
            name="ck_oracle_applications_business_process",
        ),
        sa.ForeignKeyConstraint(
            ["environment_base_url"],
            ["oracle_environment_settings.base_url"],
            name="fk_oracle_apps_environment",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "application_id",
            name="pk_oracle_applications",
        ),
        sa.UniqueConstraint(
            "environment_base_url",
            "application_name",
            name="uq_oracle_applications_environment_application",
        ),
    )
    op.create_index(
        "ix_oracle_applications_environment_active",
        "oracle_applications",
        ["environment_base_url", "is_active"],
    )
    op.create_table(
        "platform_user_applications",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("application_id", sa.BigInteger(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted_by_user_id", sa.BigInteger()),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["oracle_applications.application_id"],
            name="fk_user_apps_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"],
            ["platform_users.user_id"],
            name="fk_user_apps_granted_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["platform_users.user_id"],
            name="fk_user_apps_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id",
            "application_id",
            name="pk_platform_user_applications",
        ),
    )
    op.create_index(
        "ix_platform_user_applications_application",
        "platform_user_applications",
        ["application_id"],
    )
    op.add_column(
        "platform_sessions",
        sa.Column("active_application_id", sa.BigInteger()),
    )
    op.create_foreign_key(
        "fk_sessions_active_application",
        "platform_sessions",
        "oracle_applications",
        ["active_application_id"],
        ["application_id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_platform_sessions_active_application",
        "platform_sessions",
        ["active_application_id"],
    )

    # Preserve the existing deployment contract: the globally selected
    # application becomes the initial registered workspace for every current
    # platform user and active browser session.
    op.execute(
        sa.text(
            """
            INSERT INTO oracle_applications (
                environment_base_url,
                application_name,
                business_process,
                is_active,
                last_verified_at,
                created_at,
                updated_at
            )
            SELECT
                base_url,
                selected_application,
                CASE
                    WHEN selection_source = 'ENVIRONMENT'
                         AND COALESCE(selected_business_process, 'UNKNOWN')
                             = 'UNKNOWN'
                    THEN 'PLANNING'
                    ELSE COALESCE(selected_business_process, 'UNKNOWN')
                END,
                true,
                COALESCE(last_discovered_at, selected_at, updated_at),
                COALESCE(created_at, CURRENT_TIMESTAMP),
                COALESCE(updated_at, CURRENT_TIMESTAMP)
            FROM oracle_environment_settings
            WHERE selected_application IS NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO platform_user_applications (
                user_id,
                application_id,
                granted_at,
                granted_by_user_id
            )
            SELECT
                users.user_id,
                applications.application_id,
                CURRENT_TIMESTAMP,
                NULL
            FROM platform_users AS users
            CROSS JOIN oracle_applications AS applications
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_platform_sessions_active_application",
        table_name="platform_sessions",
    )
    op.drop_constraint(
        "fk_sessions_active_application",
        "platform_sessions",
        type_="foreignkey",
    )
    op.drop_column("platform_sessions", "active_application_id")
    op.drop_index(
        "ix_platform_user_applications_application",
        table_name="platform_user_applications",
    )
    op.drop_table("platform_user_applications")
    op.drop_index(
        "ix_oracle_applications_environment_active",
        table_name="oracle_applications",
    )
    op.drop_table("oracle_applications")
