"""Bind automation schedules to registered Oracle applications.

Revision ID: 0029_schedule_app_scope
Revises: 0028_agent_application_scope
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = "0029_schedule_app_scope"
down_revision: str | None = "0028_agent_application_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "automation_schedules",
        sa.Column("application_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_automation_schedules_application_id_oracle_applications",
        "automation_schedules",
        "oracle_applications",
        ["application_id"],
        ["application_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_automation_schedules_application",
        "automation_schedules",
        ["application_id", "is_enabled", "next_run_at"],
    )

    # Existing schedules predate workspace selection and belong to the
    # deployment application. Attribute them to the most recently selected
    # active application when one is available. Rows that cannot be proven
    # retain NULL and are hidden from application-scoped APIs.
    op.execute(
        sa.text(
            """
            UPDATE automation_schedules AS schedules
            SET application_id = selected.application_id
            FROM (
                SELECT applications.application_id
                FROM oracle_applications AS applications
                JOIN oracle_environment_settings AS settings
                  ON settings.base_url = applications.environment_base_url
                 AND settings.selected_application = applications.application_name
                WHERE applications.is_active IS TRUE
                ORDER BY settings.selected_at DESC NULLS LAST,
                         applications.application_id DESC
                LIMIT 1
            ) AS selected
            WHERE schedules.application_id IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_automation_schedules_application",
        table_name="automation_schedules",
    )
    op.drop_constraint(
        "fk_automation_schedules_application_id_oracle_applications",
        "automation_schedules",
        type_="foreignkey",
    )
    op.drop_column("automation_schedules", "application_id")
