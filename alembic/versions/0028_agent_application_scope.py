"""Bind Assistant conversations and durable work to an application.

Revision ID: 0028_agent_application_scope
Revises: 0027_task_manager_sync
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0028_agent_application_scope"
down_revision: str | None = "0027_task_manager_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column("application_id", sa.BigInteger()),
    )
    op.create_foreign_key(
        "fk_workflow_runs_application",
        "workflow_runs",
        "oracle_applications",
        ["application_id"],
        ["application_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_workflow_runs_application_started",
        "workflow_runs",
        ["application_id", "started_at"],
    )
    op.add_column(
        "execution_queue",
        sa.Column("application_id", sa.BigInteger()),
    )
    op.create_foreign_key(
        "fk_execution_queue_application",
        "execution_queue",
        "oracle_applications",
        ["application_id"],
        ["application_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_execution_queue_application_status",
        "execution_queue",
        ["application_id", "status", "created_at"],
    )
    op.drop_index(
        "uq_execution_queue_active_target",
        table_name="execution_queue",
    )
    active_execution = sa.text("status IN ('QUEUED', 'RUNNING')")
    op.create_index(
        "uq_execution_queue_active_target",
        "execution_queue",
        [sa.text("COALESCE(application_id, 0)"), "job_type", "target_key"],
        unique=True,
        postgresql_where=active_execution,
        sqlite_where=active_execution,
    )
    op.add_column(
        "agent_conversations",
        sa.Column("application_id", sa.BigInteger()),
    )
    op.create_foreign_key(
        "fk_agent_conversations_application",
        "agent_conversations",
        "oracle_applications",
        ["application_id"],
        ["application_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_agent_conversations_application_user_updated",
        "agent_conversations",
        ["application_id", "user_id", "updated_at"],
    )

    # Existing deployments had one active application contract. Associate
    # their historical conversations with the most recently configured,
    # verified application. The column remains nullable for rows that cannot
    # be attributed safely; public application-scoped APIs never return those
    # rows and all newly created conversations require a live workspace.
    op.execute(
        sa.text(
            """
            UPDATE agent_conversations
            SET application_id = (
                SELECT applications.application_id
                FROM oracle_applications AS applications
                JOIN oracle_environment_settings AS environments
                  ON environments.base_url = applications.environment_base_url
                 AND environments.selected_application = applications.application_name
                WHERE applications.is_active = true
                ORDER BY environments.updated_at DESC
                LIMIT 1
            )
            WHERE application_id IS NULL
            """
        )
    )
    for table_name in ("workflow_runs", "execution_queue"):
        op.execute(
            sa.text(
                f"""
                UPDATE {table_name}
                SET application_id = (
                    SELECT applications.application_id
                    FROM oracle_applications AS applications
                    JOIN oracle_environment_settings AS environments
                      ON environments.base_url = applications.environment_base_url
                     AND environments.selected_application = applications.application_name
                    WHERE applications.is_active = true
                    ORDER BY environments.updated_at DESC
                    LIMIT 1
                )
                WHERE application_id IS NULL
                """
            )
        )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_conversations_application_user_updated",
        table_name="agent_conversations",
    )
    op.drop_constraint(
        "fk_agent_conversations_application",
        "agent_conversations",
        type_="foreignkey",
    )
    op.drop_column("agent_conversations", "application_id")
    op.drop_index(
        "uq_execution_queue_active_target",
        table_name="execution_queue",
    )
    active_execution = sa.text("status IN ('QUEUED', 'RUNNING')")
    op.create_index(
        "uq_execution_queue_active_target",
        "execution_queue",
        ["job_type", "target_key"],
        unique=True,
        postgresql_where=active_execution,
        sqlite_where=active_execution,
    )
    op.drop_index(
        "ix_execution_queue_application_status",
        table_name="execution_queue",
    )
    op.drop_constraint(
        "fk_execution_queue_application",
        "execution_queue",
        type_="foreignkey",
    )
    op.drop_column("execution_queue", "application_id")
    op.drop_index(
        "ix_workflow_runs_application_started",
        table_name="workflow_runs",
    )
    op.drop_constraint(
        "fk_workflow_runs_application",
        "workflow_runs",
        type_="foreignkey",
    )
    op.drop_column("workflow_runs", "application_id")
