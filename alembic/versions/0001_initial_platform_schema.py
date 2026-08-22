"""Create the initial PostgreSQL platform schema.

Revision ID: 0001_platform_schema
Revises: None
Create Date: 2026-08-07
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0001_platform_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_users",
        sa.Column("user_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("username", sa.String(80), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(254)),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("failed_login_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("failed_login_count >= 0", name="ck_platform_users_failed_login_nonnegative"),
        sa.PrimaryKeyConstraint("user_id", name="pk_platform_users"),
    )
    op.create_index("uq_platform_users_username_ci", "platform_users", [sa.text("lower(username)")], unique=True)
    op.create_index(
        "uq_platform_users_email_ci",
        "platform_users",
        [sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )

    op.create_table(
        "platform_roles",
        sa.Column("role_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("role_id", name="pk_platform_roles"),
        sa.UniqueConstraint("code", name="uq_platform_roles_code"),
    )
    op.create_table(
        "platform_role_permissions",
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("permission_code", sa.String(100), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["platform_roles.role_id"], ondelete="CASCADE", name="fk_role_permissions_role"),
        sa.PrimaryKeyConstraint("role_id", "permission_code", name="pk_platform_role_permissions"),
    )
    op.create_table(
        "platform_user_roles",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_by_user_id", sa.BigInteger()),
        sa.ForeignKeyConstraint(["user_id"], ["platform_users.user_id"], ondelete="CASCADE", name="fk_user_roles_user"),
        sa.ForeignKeyConstraint(["role_id"], ["platform_roles.role_id"], ondelete="RESTRICT", name="fk_user_roles_role"),
        sa.ForeignKeyConstraint(["assigned_by_user_id"], ["platform_users.user_id"], ondelete="SET NULL", name="fk_user_roles_actor"),
        sa.PrimaryKeyConstraint("user_id", "role_id", name="pk_platform_user_roles"),
    )
    op.create_table(
        "authentication_events",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("username_snapshot", sa.String(80), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger()),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["platform_users.user_id"], ondelete="SET NULL", name="fk_authentication_events_actor"),
        sa.PrimaryKeyConstraint("event_id", name="pk_authentication_events"),
    )
    op.create_index("ix_authentication_events_occurred_at", "authentication_events", [sa.text("occurred_at DESC")])

    op.create_table(
        "oracle_pipelines",
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("code", name="pk_oracle_pipelines"),
    )
    op.create_index("uq_oracle_pipelines_code_ci", "oracle_pipelines", [sa.text("lower(code)")], unique=True)

    op.create_table(
        "planning_processes",
        sa.Column("process_code", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("process_code", name="pk_planning_processes"),
    )
    op.create_index("uq_planning_processes_code_ci", "planning_processes", [sa.text("lower(process_code)")], unique=True)
    op.create_table(
        "planning_process_versions",
        sa.Column("process_code", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("process_definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cycle_definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("version > 0", name="ck_planning_process_versions_version_positive"),
        sa.ForeignKeyConstraint(["process_code"], ["planning_processes.process_code"], ondelete="CASCADE", name="fk_process_versions_process"),
        sa.PrimaryKeyConstraint("process_code", "version", name="pk_planning_process_versions"),
    )
    op.create_index(
        "uq_planning_process_versions_active",
        "planning_process_versions",
        ["process_code"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_table(
        "process_run_profiles",
        sa.Column("profile_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("process_code", sa.String(128), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("year", sa.String(40), server_default="", nullable=False),
        sa.Column("start_period", sa.String(80), server_default="", nullable=False),
        sa.Column("end_period", sa.String(80), server_default="", nullable=False),
        sa.Column("scenario", sa.String(120)),
        sa.Column("version", sa.String(120)),
        sa.Column("pipeline_variables", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("inbox_files", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("required_upload_keys", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["process_code"], ["planning_processes.process_code"], ondelete="CASCADE", name="fk_run_profiles_process"),
        sa.PrimaryKeyConstraint("profile_id", name="pk_process_run_profiles"),
    )
    op.create_index(
        "uq_process_run_profiles_active_name",
        "process_run_profiles",
        ["process_code", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    op.create_table(
        "workflow_runs",
        sa.Column("execution_id", sa.String(64), nullable=False),
        sa.Column("workflow_name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.Column("initiated_by_username", sa.String(80)),
        sa.Column("initiated_by_display", sa.String(120)),
        sa.Column("trigger_source", sa.String(20), nullable=False),
        sa.PrimaryKeyConstraint("execution_id", name="pk_workflow_runs"),
    )
    op.create_index("ix_workflow_runs_started_at", "workflow_runs", [sa.text("started_at DESC")])
    op.create_index("ix_workflow_runs_status_started_at", "workflow_runs", ["status", sa.text("started_at DESC")])
    op.create_table(
        "workflow_steps",
        sa.Column("execution_id", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.CheckConstraint("sequence > 0", name="ck_workflow_steps_sequence_positive"),
        sa.ForeignKeyConstraint(["execution_id"], ["workflow_runs.execution_id"], ondelete="CASCADE", name="fk_workflow_steps_run"),
        sa.PrimaryKeyConstraint("execution_id", "sequence", name="pk_workflow_steps"),
    )

    op.create_table(
        "process_schedules",
        sa.Column("schedule_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("process_code", sa.String(128), nullable=False),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("first_run_local", sa.DateTime(timezone=False), nullable=False),
        sa.Column("context_mode", sa.String(30), nullable=False),
        sa.Column("run_profile_id", sa.BigInteger()),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True)),
        sa.Column("last_execution_id", sa.String(64)),
        sa.Column("last_outcome", sa.String(20), server_default="NEVER", nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["process_code"], ["planning_processes.process_code"], ondelete="CASCADE", name="fk_process_schedules_process"),
        sa.ForeignKeyConstraint(["run_profile_id"], ["process_run_profiles.profile_id"], ondelete="SET NULL", name="fk_process_schedules_profile"),
        sa.PrimaryKeyConstraint("schedule_id", name="pk_process_schedules"),
    )
    op.create_index("uq_process_schedules_active_name", "process_schedules", [sa.text("lower(name)")], unique=True, postgresql_where=sa.text("archived_at IS NULL"))
    op.create_index("ix_process_schedules_due", "process_schedules", ["next_run_at"], postgresql_where=sa.text("is_enabled IS TRUE AND archived_at IS NULL"))

    op.create_table(
        "agent_conversations",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["platform_users.user_id"], ondelete="RESTRICT", name="fk_agent_conversations_user"),
        sa.PrimaryKeyConstraint("conversation_id", name="pk_agent_conversations"),
    )
    op.create_index("ix_agent_conversations_user_updated", "agent_conversations", ["user_id", sa.text("updated_at DESC")])
    op.create_table(
        "agent_messages",
        sa.Column("message_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["agent_conversations.conversation_id"], ondelete="CASCADE", name="fk_agent_messages_conversation"),
        sa.PrimaryKeyConstraint("message_id", name="pk_agent_messages"),
    )
    op.create_index("ix_agent_messages_conversation", "agent_messages", ["conversation_id", "message_id"])
    op.create_table(
        "agent_tool_activities",
        sa.Column("activity_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("tool_name", sa.String(120), nullable=False),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["agent_conversations.conversation_id"], ondelete="CASCADE", name="fk_agent_tool_activities_conversation"),
        sa.PrimaryKeyConstraint("activity_id", name="pk_agent_tool_activities"),
    )
    op.create_index("ix_agent_tool_activities_conversation", "agent_tool_activities", ["conversation_id", "created_at"])
    op.create_table(
        "agent_action_drafts",
        sa.Column("draft_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("action_type", sa.String(40), nullable=False),
        sa.Column("target_code", sa.String(160), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(120), nullable=False),
        sa.Column("risk_level", sa.String(30), nullable=False),
        sa.Column("route", sa.String(500), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("artifact_name", sa.String(240)),
        sa.Column("required_inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stages", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("preflight_status", sa.String(50)),
        sa.Column("preflight_checks", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("preflight_at", sa.DateTime(timezone=True)),
        sa.Column("input_schema", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("input_values", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["agent_conversations.conversation_id"], ondelete="CASCADE", name="fk_agent_action_drafts_conversation"),
        sa.ForeignKeyConstraint(["message_id"], ["agent_messages.message_id"], ondelete="CASCADE", name="fk_agent_action_drafts_message"),
        sa.PrimaryKeyConstraint("draft_id", name="pk_agent_action_drafts"),
    )
    op.create_index("ix_agent_action_drafts_conversation", "agent_action_drafts", ["conversation_id", "created_at"])


def downgrade() -> None:
    op.drop_table("agent_action_drafts")
    op.drop_table("agent_tool_activities")
    op.drop_table("agent_messages")
    op.drop_table("agent_conversations")
    op.drop_table("process_schedules")
    op.drop_table("workflow_steps")
    op.drop_table("workflow_runs")
    op.drop_table("process_run_profiles")
    op.drop_table("planning_process_versions")
    op.drop_table("planning_processes")
    op.drop_table("oracle_pipelines")
    op.drop_table("authentication_events")
    op.drop_table("platform_user_roles")
    op.drop_table("platform_role_permissions")
    op.drop_table("platform_roles")
    op.drop_table("platform_users")
