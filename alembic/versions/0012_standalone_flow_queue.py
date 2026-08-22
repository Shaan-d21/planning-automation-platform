"""Allow durable standalone-flow jobs in the execution queue.

Revision ID: 0012_standalone_flow_queue
Revises: 0011_agent_action_decisions
Create Date: 2026-08-19
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0012_standalone_flow_queue"
down_revision: str | None = "0011_agent_action_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("execution_queue") as batch_op:
        batch_op.drop_constraint(
            "ck_execution_queue_job_type",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_execution_queue_job_type",
            "job_type IN ('OPERATION', 'PROCESS', 'STANDALONE_FLOW')",
        )


def downgrade() -> None:
    with op.batch_alter_table("execution_queue") as batch_op:
        batch_op.drop_constraint(
            "ck_execution_queue_job_type",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_execution_queue_job_type",
            "job_type IN ('OPERATION', 'PROCESS')",
        )
