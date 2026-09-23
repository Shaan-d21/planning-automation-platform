"""Canonical SQLAlchemy Core schema for BISP EPM Automation."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    and_,
    func,
    or_,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB


metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

IDENTITY_BIGINT = BigInteger().with_variant(Integer, "sqlite")
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")
UTC_TIMESTAMP = DateTime(timezone=True)


platform_users = Table(
    "platform_users",
    metadata,
    Column("user_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column("username", String(80), nullable=False),
    Column("display_name", String(120), nullable=False),
    Column("email", String(254)),
    # Federated identities deliberately have no local password credential.
    Column("password_hash", Text),
    Column("is_active", Boolean, nullable=False, server_default=text("true")),
    Column("failed_login_count", Integer, nullable=False, server_default="0"),
    Column("locked_until", UTC_TIMESTAMP),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("updated_at", UTC_TIMESTAMP, nullable=False),
    Column("last_login_at", UTC_TIMESTAMP),
    CheckConstraint("failed_login_count >= 0", name="failed_login_nonnegative"),
)


oracle_environment_settings = Table(
    "oracle_environment_settings",
    metadata,
    Column("base_url", String(500), primary_key=True),
    Column("deployment_mode", String(32), nullable=False),
    Column("selected_application", String(128)),
    Column("selection_source", String(32)),
    Column(
        "discovered_applications",
        JSON_DOCUMENT,
        nullable=False,
        server_default=text("'[]'"),
    ),
    Column("last_discovered_at", UTC_TIMESTAMP),
    Column("last_discovery_error", Text),
    Column("selected_at", UTC_TIMESTAMP),
    Column(
        "selected_by_user_id",
        IDENTITY_BIGINT,
    ),
    Column(
        "created_at",
        UTC_TIMESTAMP,
        nullable=False,
        server_default=func.now(),
    ),
    Column(
        "updated_at",
        UTC_TIMESTAMP,
        nullable=False,
        server_default=func.now(),
    ),
    CheckConstraint(
        "selection_source IS NULL OR selection_source IN "
        "('DATABASE', 'ENVIRONMENT', 'AUTO_DISCOVERY', 'ADMIN_SELECTION')",
        name="selection_source",
    ),
    ForeignKeyConstraint(
        ["selected_by_user_id"],
        ["platform_users.user_id"],
        name="fk_oracle_env_selected_user",
        ondelete="SET NULL",
    ),
)
Index("uq_platform_users_username_ci", func.lower(platform_users.c.username), unique=True)
Index(
    "uq_platform_users_email_ci",
    func.lower(platform_users.c.email),
    unique=True,
    postgresql_where=platform_users.c.email.is_not(None),
    sqlite_where=platform_users.c.email.is_not(None),
)

platform_roles = Table(
    "platform_roles",
    metadata,
    Column("role_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column("code", String(64), nullable=False, unique=True),
    Column("name", String(120), nullable=False),
    Column("description", Text, nullable=False),
    Column("is_system", Boolean, nullable=False, server_default=text("true")),
)

platform_role_permissions = Table(
    "platform_role_permissions",
    metadata,
    Column(
        "role_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_roles.role_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("permission_code", String(100), primary_key=True),
)

platform_user_roles = Table(
    "platform_user_roles",
    metadata,
    Column(
        "user_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_users.user_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "role_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_roles.role_id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("assigned_at", UTC_TIMESTAMP, nullable=False),
    Column(
        "assigned_by_user_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_users.user_id", ondelete="SET NULL"),
    ),
)

authentication_events = Table(
    "authentication_events",
    metadata,
    Column("event_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column("event_type", String(40), nullable=False),
    Column("username_snapshot", String(80), nullable=False),
    Column(
        "actor_user_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_users.user_id", ondelete="SET NULL"),
    ),
    Column("success", Boolean, nullable=False),
    Column("ip_address", String(45)),
    Column("occurred_at", UTC_TIMESTAMP, nullable=False),
    Column("details", JSON_DOCUMENT, nullable=False, server_default=text("'{}'")),
)
Index("ix_authentication_events_occurred_at", authentication_events.c.occurred_at.desc())

identity_providers = Table(
    "identity_providers",
    metadata,
    Column("provider_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column("code", String(64), nullable=False, unique=True),
    Column("provider_type", String(32), nullable=False),
    Column("display_name", String(120), nullable=False),
    Column("issuer_url", String(500)),
    Column("environment_key", String(64)),
    Column("is_enabled", Boolean, nullable=False, server_default=text("true")),
    Column(
        "safe_configuration",
        JSON_DOCUMENT,
        nullable=False,
        server_default=text("'{}'"),
    ),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("updated_at", UTC_TIMESTAMP, nullable=False),
    CheckConstraint(
        "provider_type IN ('LOCAL', 'ORACLE_CLOUD', 'OIDC', 'LDAP')",
        name="provider_type",
    ),
)
Index(
    "uq_identity_providers_code_ci",
    func.lower(identity_providers.c.code),
    unique=True,
)

external_identities = Table(
    "external_identities",
    metadata,
    Column(
        "external_identity_id",
        IDENTITY_BIGINT,
        primary_key=True,
        autoincrement=True,
    ),
    Column(
        "provider_id",
        IDENTITY_BIGINT,
        ForeignKey("identity_providers.provider_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "user_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_users.user_id", ondelete="SET NULL"),
    ),
    Column("subject", String(255), nullable=False),
    # Immutable OIDC subject bound only after a validated first sign-in.
    Column("authenticated_subject", String(255)),
    Column("username", String(254), nullable=False),
    Column("display_name", String(200), nullable=False),
    Column("email", String(254)),
    Column("is_active", Boolean, nullable=False, server_default=text("true")),
    Column("last_seen_at", UTC_TIMESTAMP),
    Column("last_synced_at", UTC_TIMESTAMP, nullable=False),
    Column("last_authenticated_at", UTC_TIMESTAMP),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("updated_at", UTC_TIMESTAMP, nullable=False),
    UniqueConstraint("provider_id", "subject", name="provider_subject"),
    UniqueConstraint("provider_id", "user_id", name="provider_user"),
)
Index(
    "ix_external_identities_provider_active",
    external_identities.c.provider_id,
    external_identities.c.is_active,
)
Index("ix_external_identities_user", external_identities.c.user_id)
Index(
    "uq_external_identities_provider_authenticated_subject",
    external_identities.c.provider_id,
    external_identities.c.authenticated_subject,
    unique=True,
)

external_entitlements = Table(
    "external_entitlements",
    metadata,
    Column("entitlement_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column(
        "provider_id",
        IDENTITY_BIGINT,
        ForeignKey("identity_providers.provider_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("entitlement_type", String(32), nullable=False),
    Column("external_key", String(255), nullable=False),
    Column("display_name", String(255), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("updated_at", UTC_TIMESTAMP, nullable=False),
    CheckConstraint(
        "entitlement_type IN ('APPLICATION_ROLE', 'GRANULAR_ROLE', 'GROUP')",
        name="entitlement_type",
    ),
    UniqueConstraint(
        "provider_id",
        "entitlement_type",
        "external_key",
        name="provider_type_key",
    ),
)

external_identity_entitlements = Table(
    "external_identity_entitlements",
    metadata,
    Column(
        "external_identity_id",
        IDENTITY_BIGINT,
        ForeignKey("external_identities.external_identity_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "entitlement_id",
        IDENTITY_BIGINT,
        ForeignKey("external_entitlements.entitlement_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("assignment_type", String(16), nullable=False),
    Column("granted_through_group", String(255)),
    Column("observed_at", UTC_TIMESTAMP, nullable=False),
    CheckConstraint(
        "assignment_type IN ('DIRECT', 'INHERITED')",
        name="assignment_type",
    ),
)

identity_role_mappings = Table(
    "identity_role_mappings",
    metadata,
    Column("mapping_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column(
        "provider_id",
        IDENTITY_BIGINT,
        ForeignKey("identity_providers.provider_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "entitlement_id",
        IDENTITY_BIGINT,
        ForeignKey("external_entitlements.entitlement_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    Column(
        "role_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_roles.role_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("is_enabled", Boolean, nullable=False, server_default=text("true")),
    Column(
        "created_by_user_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_users.user_id", ondelete="SET NULL"),
    ),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("updated_at", UTC_TIMESTAMP, nullable=False),
    UniqueConstraint("provider_id", "entitlement_id", name="provider_entitlement"),
)

identity_sync_runs = Table(
    "identity_sync_runs",
    metadata,
    Column("sync_run_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column(
        "provider_id",
        IDENTITY_BIGINT,
        ForeignKey("identity_providers.provider_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("status", String(16), nullable=False),
    Column("started_at", UTC_TIMESTAMP, nullable=False),
    Column("completed_at", UTC_TIMESTAMP),
    Column("identities_seen", Integer, nullable=False, server_default="0"),
    Column("identities_linked", Integer, nullable=False, server_default="0"),
    Column("identities_deactivated", Integer, nullable=False, server_default="0"),
    Column("entitlements_seen", Integer, nullable=False, server_default="0"),
    Column("error_summary", Text),
    Column(
        "initiated_by_user_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_users.user_id", ondelete="SET NULL"),
    ),
    Column("details", JSON_DOCUMENT, nullable=False, server_default=text("'{}'")),
    CheckConstraint(
        "status IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'FAILED')",
        name="status",
    ),
    CheckConstraint("identities_seen >= 0", name="identities_seen_nonnegative"),
    CheckConstraint("identities_linked >= 0", name="identities_linked_nonnegative"),
    CheckConstraint(
        "identities_deactivated >= 0",
        name="identities_deactivated_nonnegative",
    ),
    CheckConstraint("entitlements_seen >= 0", name="entitlements_seen_nonnegative"),
)
Index(
    "ix_identity_sync_runs_provider_started",
    identity_sync_runs.c.provider_id,
    identity_sync_runs.c.started_at.desc(),
)

api_tokens = Table(
    "api_tokens",
    metadata,
    Column("token_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column(
        "user_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_users.user_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("name", String(120), nullable=False),
    Column("token_prefix", String(20), nullable=False, unique=True),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("scopes", JSON_DOCUMENT, nullable=False),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("expires_at", UTC_TIMESTAMP),
    Column("last_used_at", UTC_TIMESTAMP),
    Column("last_used_ip", String(45)),
    Column("revoked_at", UTC_TIMESTAMP),
)
Index(
    "ix_api_tokens_user_active",
    api_tokens.c.user_id,
    api_tokens.c.revoked_at,
)

oracle_artifacts = Table(
    "oracle_artifacts",
    metadata,
    Column("artifact_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column("environment_key", String(64), nullable=False),
    Column("environment_base_url", String(500), nullable=False),
    Column("application_name", String(128), nullable=False),
    Column("artifact_type", String(32), nullable=False),
    Column("oracle_identifier", String(250), nullable=False),
    Column("normalized_identifier", String(250), nullable=False),
    Column("display_name", String(250), nullable=False),
    Column("description", Text),
    Column("source", String(32), nullable=False),
    Column("verification_status", String(32), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default=text("true")),
    Column(
        "consecutive_missing_count",
        Integer,
        nullable=False,
        server_default="0",
    ),
    Column("last_verified_at", UTC_TIMESTAMP),
    Column("last_error", Text),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("updated_at", UTC_TIMESTAMP, nullable=False),
    UniqueConstraint(
        "environment_key",
        "artifact_type",
        "normalized_identifier",
        name="uq_oracle_artifacts_environment_type_identifier",
    ),
    CheckConstraint(
        "artifact_type IN ('PIPELINE', 'DATA_INTEGRATION', "
        "'BUSINESS_RULE', 'DATA_MAP', 'METADATA_IMPORT_JOB', "
        "'DATA_IMPORT_JOB', 'CUBE_REFRESH_JOB', 'CUBE')",
        name="artifact_type",
    ),
    CheckConstraint(
        "verification_status IN "
        "('VERIFIED', 'PENDING', 'MISSING', 'UNAVAILABLE', 'INACTIVE')",
        name="verification_status",
    ),
    CheckConstraint(
        "consecutive_missing_count >= 0",
        name="missing_count_nonnegative",
    ),
)
Index(
    "ix_oracle_artifacts_environment_type_status",
    oracle_artifacts.c.environment_key,
    oracle_artifacts.c.artifact_type,
    oracle_artifacts.c.verification_status,
)

business_rule_rtp_sync_runs = Table(
    "business_rule_rtp_sync_runs",
    metadata,
    Column("sync_run_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column("environment_key", String(64), nullable=False),
    Column("environment_base_url", String(500), nullable=False),
    Column("application_name", String(128), nullable=False),
    Column("source_name", String(500), nullable=False),
    Column("source_checksum", String(64), nullable=False),
    Column("parser_version", String(32), nullable=False),
    Column("status", String(20), nullable=False),
    Column("rules_imported", Integer, nullable=False, server_default="0"),
    Column("prompts_imported", Integer, nullable=False, server_default="0"),
    Column("warnings", JSON_DOCUMENT, nullable=False, server_default=text("'[]'")),
    Column("error_summary", Text),
    Column("started_at", UTC_TIMESTAMP, nullable=False),
    Column("completed_at", UTC_TIMESTAMP),
    CheckConstraint(
        "status IN ('RUNNING', 'COMPLETED', 'FAILED')",
        name="status",
    ),
    CheckConstraint("rules_imported >= 0", name="rules_nonnegative"),
    CheckConstraint("prompts_imported >= 0", name="prompts_nonnegative"),
)
Index(
    "ix_business_rule_rtp_sync_runs_environment_started",
    business_rule_rtp_sync_runs.c.environment_key,
    business_rule_rtp_sync_runs.c.started_at.desc(),
)

business_rule_rtp_definitions = Table(
    "business_rule_rtp_definitions",
    metadata,
    Column("definition_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column("environment_key", String(64), nullable=False),
    Column("application_name", String(128), nullable=False),
    Column("rule_name", String(250), nullable=False),
    Column("normalized_rule_name", String(250), nullable=False),
    Column("cube_name", String(128)),
    Column("source_name", String(500), nullable=False),
    Column("source_path", String(1000), nullable=False),
    Column("source_checksum", String(64), nullable=False),
    Column("parser_version", String(32), nullable=False),
    Column("definition_checksum", String(64), nullable=False),
    Column(
        "source_sync_run_id",
        IDENTITY_BIGINT,
        ForeignKey("business_rule_rtp_sync_runs.sync_run_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("is_active", Boolean, nullable=False, server_default=text("true")),
    Column("synchronized_at", UTC_TIMESTAMP, nullable=False),
    UniqueConstraint(
        "environment_key",
        "normalized_rule_name",
        name="environment_rule",
    ),
)
Index(
    "ix_business_rule_rtp_definitions_environment_active",
    business_rule_rtp_definitions.c.environment_key,
    business_rule_rtp_definitions.c.is_active,
)

business_rule_rtp_parameters = Table(
    "business_rule_rtp_parameters",
    metadata,
    Column("parameter_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column(
        "definition_id",
        IDENTITY_BIGINT,
        ForeignKey("business_rule_rtp_definitions.definition_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("prompt_order", Integer, nullable=False),
    Column("name", String(250), nullable=False),
    Column("normalized_name", String(250), nullable=False),
    Column("label", String(500), nullable=False),
    Column("value_type", String(80), nullable=False),
    Column("dimension", String(128)),
    Column("default_value", Text),
    Column("has_default", Boolean, nullable=False, server_default=text("false")),
    Column("required_at_launch", Boolean, nullable=False, server_default=text("true")),
    Column("is_hidden", Boolean, nullable=False, server_default=text("false")),
    Column("allow_multiple", Boolean, nullable=False, server_default=text("false")),
    Column("security_mode", String(80)),
    Column("scope_type", String(20), nullable=False, server_default="UNKNOWN"),
    Column("scope_name", String(500)),
    Column("source_variable_id", String(100)),
    Column("limit_type", String(80)),
    Column("limit_value", Text),
    Column("source_metadata", JSON_DOCUMENT, nullable=False, server_default=text("'{}'")),
    UniqueConstraint("definition_id", "normalized_name", name="definition_name"),
    UniqueConstraint("definition_id", "prompt_order", name="definition_order"),
    CheckConstraint("prompt_order > 0", name="order_positive"),
)
Index(
    "ix_business_rule_rtp_parameters_definition_order",
    business_rule_rtp_parameters.c.definition_id,
    business_rule_rtp_parameters.c.prompt_order,
)

planning_processes = Table(
    "planning_processes",
    metadata,
    Column("process_code", String(128), primary_key=True),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("updated_at", UTC_TIMESTAMP, nullable=False),
)
Index("uq_planning_processes_code_ci", func.lower(planning_processes.c.process_code), unique=True)

planning_process_versions = Table(
    "planning_process_versions",
    metadata,
    Column(
        "process_code",
        String(128),
        ForeignKey("planning_processes.process_code", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("version", Integer, primary_key=True),
    Column("status", String(20), nullable=False),
    Column("process_definition", JSON_DOCUMENT, nullable=False),
    Column("cycle_definition", JSON_DOCUMENT, nullable=False),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("activated_at", UTC_TIMESTAMP),
    CheckConstraint("version > 0", name="version_positive"),
)
Index(
    "uq_planning_process_versions_active",
    planning_process_versions.c.process_code,
    unique=True,
    postgresql_where=planning_process_versions.c.status == "ACTIVE",
    sqlite_where=planning_process_versions.c.status == "ACTIVE",
)

process_run_profiles = Table(
    "process_run_profiles",
    metadata,
    Column("profile_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column(
        "process_code",
        String(128),
        ForeignKey("planning_processes.process_code", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("name", String(160), nullable=False),
    Column("year", String(40), nullable=False, server_default=""),
    Column("start_period", String(80), nullable=False, server_default=""),
    Column("end_period", String(80), nullable=False, server_default=""),
    Column("scenario", String(120)),
    Column("version", String(120)),
    Column("pipeline_variables", JSON_DOCUMENT, nullable=False, server_default=text("'{}'")),
    Column("inbox_files", JSON_DOCUMENT, nullable=False, server_default=text("'{}'")),
    Column("required_upload_keys", JSON_DOCUMENT, nullable=False, server_default=text("'[]'")),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("archived_at", UTC_TIMESTAMP),
)
Index(
    "uq_process_run_profiles_active_name",
    process_run_profiles.c.process_code,
    func.lower(process_run_profiles.c.name),
    unique=True,
    postgresql_where=process_run_profiles.c.archived_at.is_(None),
    sqlite_where=process_run_profiles.c.archived_at.is_(None),
)

workflow_runs = Table(
    "workflow_runs",
    metadata,
    Column("execution_id", String(64), primary_key=True),
    Column("workflow_name", String(200), nullable=False),
    Column("status", String(20), nullable=False),
    Column("started_at", UTC_TIMESTAMP, nullable=False),
    Column("completed_at", UTC_TIMESTAMP),
    Column("error_message", Text),
    Column("initiated_by_username", String(80)),
    Column("initiated_by_display", String(120)),
    Column("trigger_source", String(20), nullable=False),
    Column("oracle_execution_username", String(254)),
)
Index("ix_workflow_runs_started_at", workflow_runs.c.started_at.desc())
Index("ix_workflow_runs_status_started_at", workflow_runs.c.status, workflow_runs.c.started_at.desc())

execution_queue = Table(
    "execution_queue",
    metadata,
    Column("execution_id", String(64), primary_key=True),
    Column("job_type", String(20), nullable=False),
    Column("target_key", String(300), nullable=False),
    Column("payload", JSON_DOCUMENT, nullable=False),
    Column("status", String(30), nullable=False),
    Column("priority", Integer, nullable=False, server_default="100"),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("available_at", UTC_TIMESTAMP, nullable=False),
    Column("claimed_at", UTC_TIMESTAMP),
    Column("lease_owner", String(160)),
    Column("lease_expires_at", UTC_TIMESTAMP),
    Column("heartbeat_at", UTC_TIMESTAMP),
    Column("completed_at", UTC_TIMESTAMP),
    Column("error_message", Text),
    Column("cancellation_requested_at", UTC_TIMESTAMP),
    Column("cancellation_requested_by", String(80)),
    CheckConstraint(
        "job_type IN ('OPERATION', 'PROCESS', 'STANDALONE_FLOW')",
        name="job_type",
    ),
    CheckConstraint(
        "status IN ('QUEUED', 'RUNNING', 'SUCCESS', 'FAILED', "
        "'RECOVERY_REQUIRED', 'CANCELLED')",
        name="status",
    ),
    CheckConstraint("attempt_count >= 0", name="attempt_nonnegative"),
)
Index(
    "ix_execution_queue_claim",
    execution_queue.c.status,
    execution_queue.c.available_at,
    execution_queue.c.priority,
    execution_queue.c.created_at,
)
Index(
    "ix_execution_queue_lease",
    execution_queue.c.lease_expires_at,
    postgresql_where=execution_queue.c.status == "RUNNING",
    sqlite_where=execution_queue.c.status == "RUNNING",
)
Index(
    "uq_execution_queue_active_target",
    execution_queue.c.job_type,
    func.lower(execution_queue.c.target_key),
    unique=True,
    postgresql_where=or_(
        execution_queue.c.status == "QUEUED",
        execution_queue.c.status == "RUNNING",
    ),
    sqlite_where=or_(
        execution_queue.c.status == "QUEUED",
        execution_queue.c.status == "RUNNING",
    ),
)

workflow_steps = Table(
    "workflow_steps",
    metadata,
    Column(
        "execution_id",
        String(64),
        ForeignKey("workflow_runs.execution_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("sequence", Integer, primary_key=True),
    Column("name", String(200), nullable=False),
    Column("status", String(20), nullable=False),
    Column("started_at", UTC_TIMESTAMP),
    Column("completed_at", UTC_TIMESTAMP),
    Column("details", JSON_DOCUMENT, nullable=False, server_default=text("'{}'")),
    Column("error_message", Text),
    CheckConstraint("sequence > 0", name="sequence_positive"),
)

process_schedules = Table(
    "process_schedules",
    metadata,
    Column("schedule_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column("name", String(160), nullable=False),
    Column(
        "process_code",
        String(128),
        ForeignKey("planning_processes.process_code", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("frequency", String(20), nullable=False),
    Column("timezone", String(64), nullable=False),
    Column("first_run_local", DateTime(timezone=False), nullable=False),
    Column("context_mode", String(30), nullable=False),
    Column(
        "run_profile_id",
        IDENTITY_BIGINT,
        ForeignKey("process_run_profiles.profile_id", ondelete="SET NULL"),
    ),
    Column("is_enabled", Boolean, nullable=False, server_default=text("true")),
    Column("next_run_at", UTC_TIMESTAMP),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("updated_at", UTC_TIMESTAMP, nullable=False),
    Column("last_triggered_at", UTC_TIMESTAMP),
    Column(
        "last_execution_id",
        String(64),
    ),
    Column("last_outcome", String(20), nullable=False, server_default="NEVER"),
    Column("last_error", Text),
    Column("archived_at", UTC_TIMESTAMP),
)
Index(
    "uq_process_schedules_active_name",
    func.lower(process_schedules.c.name),
    unique=True,
    postgresql_where=process_schedules.c.archived_at.is_(None),
    sqlite_where=process_schedules.c.archived_at.is_(None),
)
Index(
    "ix_process_schedules_due",
    process_schedules.c.next_run_at,
    postgresql_where=and_(process_schedules.c.is_enabled.is_(True), process_schedules.c.archived_at.is_(None)),
    sqlite_where=and_(process_schedules.c.is_enabled.is_(True), process_schedules.c.archived_at.is_(None)),
)

automation_schedules = Table(
    "automation_schedules",
    metadata,
    Column("schedule_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column("environment_key", String(64), nullable=False),
    Column("name", String(160), nullable=False),
    Column("target_type", String(32), nullable=False),
    Column("target_key", String(300), nullable=False),
    Column("frequency", String(20), nullable=False),
    Column("timezone", String(64), nullable=False),
    Column("first_run_local", DateTime(timezone=False), nullable=False),
    Column("input_policy", String(24), nullable=False),
    Column("configuration", JSON_DOCUMENT, nullable=False, server_default=text("'{}'")),
    Column("concurrency_policy", String(24), nullable=False),
    Column("misfire_policy", String(20), nullable=False),
    Column("is_enabled", Boolean, nullable=False, server_default=text("true")),
    Column("next_run_at", UTC_TIMESTAMP),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("updated_at", UTC_TIMESTAMP, nullable=False),
    Column("last_triggered_at", UTC_TIMESTAMP),
    Column("last_execution_id", String(64)),
    Column("last_outcome", String(20), nullable=False, server_default="NEVER"),
    Column("last_error", Text),
    Column("archived_at", UTC_TIMESTAMP),
    CheckConstraint(
        "target_type IN ('ORACLE_PIPELINE', 'RTP_REGISTRY_SYNC')",
        name="target_type",
    ),
    CheckConstraint(
        "frequency IN ('ONE_TIME', 'DAILY', 'WEEKLY', 'MONTHLY')",
        name="frequency",
    ),
    CheckConstraint(
        "input_policy IN ('ORACLE_DEFAULTS', 'FIXED', 'DYNAMIC')",
        name="input_policy",
    ),
    CheckConstraint(
        "concurrency_policy IN ('SKIP_IF_ACTIVE')",
        name="concurrency_policy",
    ),
    CheckConstraint(
        "misfire_policy IN ('RUN_ONCE', 'SKIP')",
        name="misfire_policy",
    ),
    CheckConstraint(
        "last_outcome IN ('NEVER', 'CLAIMED', 'SUBMITTED', 'COMPLETED', "
        "'FAILED', 'SKIPPED')",
        name="last_outcome",
    ),
)
Index(
    "uq_automation_schedules_environment_name",
    automation_schedules.c.environment_key,
    func.lower(automation_schedules.c.name),
    unique=True,
    postgresql_where=automation_schedules.c.archived_at.is_(None),
    sqlite_where=automation_schedules.c.archived_at.is_(None),
)
Index(
    "ix_automation_schedules_due",
    automation_schedules.c.next_run_at,
    postgresql_where=and_(
        automation_schedules.c.is_enabled.is_(True),
        automation_schedules.c.archived_at.is_(None),
    ),
    sqlite_where=and_(
        automation_schedules.c.is_enabled.is_(True),
        automation_schedules.c.archived_at.is_(None),
    ),
)
Index(
    "ix_automation_schedules_environment_target",
    automation_schedules.c.environment_key,
    automation_schedules.c.target_type,
    automation_schedules.c.target_key,
)

automation_schedule_runs = Table(
    "automation_schedule_runs",
    metadata,
    Column("run_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column(
        "schedule_id",
        IDENTITY_BIGINT,
        ForeignKey("automation_schedules.schedule_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("scheduled_for", UTC_TIMESTAMP, nullable=False),
    Column("claimed_at", UTC_TIMESTAMP, nullable=False),
    Column("completed_at", UTC_TIMESTAMP),
    Column("status", String(20), nullable=False),
    Column("execution_id", String(64)),
    Column("resolved_payload", JSON_DOCUMENT, nullable=False, server_default=text("'{}'")),
    Column("error_message", Text),
    CheckConstraint(
        "status IN ('CLAIMED', 'SUBMITTED', 'COMPLETED', 'FAILED', 'SKIPPED')",
        name="status",
    ),
    UniqueConstraint(
        "schedule_id",
        "scheduled_for",
        name="uq_automation_schedule_runs_occurrence",
    ),
)
Index(
    "ix_automation_schedule_runs_schedule_time",
    automation_schedule_runs.c.schedule_id,
    automation_schedule_runs.c.scheduled_for.desc(),
)

planning_cycles = Table(
    "planning_cycles",
    metadata,
    Column("cycle_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column("code", String(128), nullable=False),
    Column("name", String(200), nullable=False),
    Column("cycle_type", String(40), nullable=False),
    Column(
        "process_code",
        String(128),
        ForeignKey("planning_processes.process_code", ondelete="SET NULL"),
    ),
    Column("scenario", String(120)),
    Column("year", String(40), nullable=False),
    Column("actual_through_period", String(80)),
    Column("forecast_start_period", String(80)),
    Column("start_date", Date, nullable=False),
    Column("due_date", Date, nullable=False),
    Column("status", String(30), nullable=False),
    Column(
        "created_by_user_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_users.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("updated_at", UTC_TIMESTAMP, nullable=False),
    Column("completed_at", UTC_TIMESTAMP),
    Column("archived_at", UTC_TIMESTAMP),
    CheckConstraint("due_date >= start_date", name="date_order"),
)
Index(
    "uq_planning_cycles_active_code",
    func.lower(planning_cycles.c.code),
    unique=True,
    postgresql_where=planning_cycles.c.archived_at.is_(None),
    sqlite_where=planning_cycles.c.archived_at.is_(None),
)
Index(
    "ix_planning_cycles_status_due",
    planning_cycles.c.status,
    planning_cycles.c.due_date,
)

planning_cycle_stages = Table(
    "planning_cycle_stages",
    metadata,
    Column("stage_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column(
        "cycle_id",
        IDENTITY_BIGINT,
        ForeignKey("planning_cycles.cycle_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("sequence", Integer, nullable=False),
    Column("code", String(80), nullable=False),
    Column("name", String(160), nullable=False),
    Column("status", String(30), nullable=False),
    Column("start_date", Date),
    Column("due_date", Date),
    Column("completed_at", UTC_TIMESTAMP),
    CheckConstraint("sequence > 0", name="sequence_positive"),
    CheckConstraint(
        "due_date IS NULL OR start_date IS NULL OR due_date >= start_date",
        name="date_order",
    ),
)
Index(
    "uq_planning_cycle_stages_code_ci",
    planning_cycle_stages.c.cycle_id,
    func.lower(planning_cycle_stages.c.code),
    unique=True,
)
Index(
    "uq_planning_cycle_stages_sequence",
    planning_cycle_stages.c.cycle_id,
    planning_cycle_stages.c.sequence,
    unique=True,
)

planning_tasks = Table(
    "planning_tasks",
    metadata,
    Column("task_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column(
        "stage_id",
        IDENTITY_BIGINT,
        ForeignKey("planning_cycle_stages.stage_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("title", String(200), nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("task_type", String(60), nullable=False),
    Column("status", String(30), nullable=False),
    Column("priority", String(20), nullable=False),
    Column(
        "assigned_user_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_users.user_id", ondelete="RESTRICT"),
    ),
    Column(
        "assigned_role_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_roles.role_id", ondelete="RESTRICT"),
    ),
    Column("entity", String(160)),
    Column("scenario", String(120)),
    Column("period", String(80)),
    Column("due_at", UTC_TIMESTAMP),
    Column("action_type", String(60), nullable=False),
    Column("action_config", JSON_DOCUMENT, nullable=False, server_default=text("'{}'")),
    Column(
        "created_by_user_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_users.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("updated_at", UTC_TIMESTAMP, nullable=False),
    Column("completed_at", UTC_TIMESTAMP),
    CheckConstraint(
        "(assigned_user_id IS NOT NULL AND assigned_role_id IS NULL) OR "
        "(assigned_user_id IS NULL AND assigned_role_id IS NOT NULL)",
        name="one_assignee",
    ),
)
Index(
    "ix_planning_tasks_user_status_due",
    planning_tasks.c.assigned_user_id,
    planning_tasks.c.status,
    planning_tasks.c.due_at,
)
Index(
    "ix_planning_tasks_role_status_due",
    planning_tasks.c.assigned_role_id,
    planning_tasks.c.status,
    planning_tasks.c.due_at,
)
Index("ix_planning_tasks_stage", planning_tasks.c.stage_id)

planning_task_dependencies = Table(
    "planning_task_dependencies",
    metadata,
    Column(
        "task_id",
        IDENTITY_BIGINT,
        ForeignKey("planning_tasks.task_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "depends_on_task_id",
        IDENTITY_BIGINT,
        ForeignKey("planning_tasks.task_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    CheckConstraint("task_id <> depends_on_task_id", name="not_self"),
)

planning_task_executions = Table(
    "planning_task_executions",
    metadata,
    Column(
        "task_execution_id",
        IDENTITY_BIGINT,
        primary_key=True,
        autoincrement=True,
    ),
    Column(
        "task_id",
        IDENTITY_BIGINT,
        ForeignKey("planning_tasks.task_id", ondelete="CASCADE"),
        nullable=False,
    ),
    # The operation is queued before workflow_runs is persisted, so this
    # durable correlation intentionally cannot use an immediate foreign key.
    Column("execution_id", String(64), nullable=False, unique=True),
    Column("attempt_number", Integer, nullable=False),
    Column("status", String(20), nullable=False),
    Column(
        "initiated_by_user_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_users.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("linked_at", UTC_TIMESTAMP, nullable=False),
    Column("updated_at", UTC_TIMESTAMP, nullable=False),
    Column("completed_at", UTC_TIMESTAMP),
    Column("error_message", Text),
    CheckConstraint("attempt_number > 0", name="attempt_positive"),
)
Index(
    "uq_planning_task_executions_attempt",
    planning_task_executions.c.task_id,
    planning_task_executions.c.attempt_number,
    unique=True,
)
Index(
    "ix_planning_task_executions_task_linked",
    planning_task_executions.c.task_id,
    planning_task_executions.c.linked_at.desc(),
)

planning_task_validations = Table(
    "planning_task_validations",
    metadata,
    Column("validation_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column(
        "task_id",
        IDENTITY_BIGINT,
        ForeignKey("planning_tasks.task_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("validation_type", String(20), nullable=False),
    Column("status", String(20), nullable=False),
    Column("source_cube", String(128), nullable=False),
    Column("target_cube", String(128)),
    Column("selection", JSON_DOCUMENT, nullable=False),
    Column("criteria", JSON_DOCUMENT, nullable=False),
    Column("checked_cells", Integer, nullable=False),
    Column("matched_cells", Integer),
    Column("exception_count", Integer, nullable=False),
    Column("warning_count", Integer, nullable=False, server_default="0"),
    Column(
        "performed_by_user_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_users.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("performed_at", UTC_TIMESTAMP, nullable=False),
    Column(
        "warning_acknowledged_by_user_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_users.user_id", ondelete="RESTRICT"),
    ),
    Column("warning_acknowledged_at", UTC_TIMESTAMP),
    CheckConstraint("checked_cells >= 0", name="checked_cells_nonnegative"),
    CheckConstraint("exception_count >= 0", name="exception_count_nonnegative"),
    CheckConstraint("warning_count >= 0", name="warning_count_nonnegative"),
)
Index(
    "ix_planning_task_validations_task_performed",
    planning_task_validations.c.task_id,
    planning_task_validations.c.performed_at.desc(),
)

planning_approvals = Table(
    "planning_approvals",
    metadata,
    Column("approval_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column(
        "cycle_id",
        IDENTITY_BIGINT,
        ForeignKey("planning_cycles.cycle_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "submitted_task_id",
        IDENTITY_BIGINT,
        ForeignKey("planning_tasks.task_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "approval_task_id",
        IDENTITY_BIGINT,
        ForeignKey("planning_tasks.task_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "validation_id",
        IDENTITY_BIGINT,
        ForeignKey("planning_task_validations.validation_id", ondelete="SET NULL"),
    ),
    Column("status", String(30), nullable=False),
    Column(
        "submitted_by_user_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_users.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("submitted_at", UTC_TIMESTAMP, nullable=False),
    Column(
        "decided_by_user_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_users.user_id", ondelete="RESTRICT"),
    ),
    Column("decided_at", UTC_TIMESTAMP),
    Column("decision_comment", Text),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("updated_at", UTC_TIMESTAMP, nullable=False),
)
Index(
    "uq_planning_approvals_pending_task",
    planning_approvals.c.approval_task_id,
    unique=True,
    postgresql_where=planning_approvals.c.status == "PENDING",
    sqlite_where=planning_approvals.c.status == "PENDING",
)
Index(
    "ix_planning_approvals_cycle_status",
    planning_approvals.c.cycle_id,
    planning_approvals.c.status,
)

user_notifications = Table(
    "user_notifications",
    metadata,
    Column("notification_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column(
        "recipient_user_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_users.user_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("event_type", String(60), nullable=False),
    Column("severity", String(20), nullable=False),
    Column("title", String(200), nullable=False),
    Column("message", Text, nullable=False),
    Column("action_url", String(500)),
    Column("source_type", String(60)),
    Column("source_id", String(100)),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("read_at", UTC_TIMESTAMP),
)
Index(
    "ix_user_notifications_recipient_created",
    user_notifications.c.recipient_user_id,
    user_notifications.c.created_at.desc(),
)
Index(
    "ix_user_notifications_recipient_unread",
    user_notifications.c.recipient_user_id,
    user_notifications.c.read_at,
)

agent_conversations = Table(
    "agent_conversations",
    metadata,
    Column("conversation_id", Uuid(as_uuid=False), primary_key=True),
    Column(
        "user_id",
        IDENTITY_BIGINT,
        ForeignKey("platform_users.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("title", String(160), nullable=False),
    Column("provider", String(40), nullable=False),
    Column("model", String(120), nullable=False),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("updated_at", UTC_TIMESTAMP, nullable=False),
)
Index("ix_agent_conversations_user_updated", agent_conversations.c.user_id, agent_conversations.c.updated_at.desc())

agent_messages = Table(
    "agent_messages",
    metadata,
    Column("message_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column(
        "conversation_id",
        Uuid(as_uuid=False),
        ForeignKey("agent_conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("role", String(20), nullable=False),
    Column("content", Text, nullable=False),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
)
Index("ix_agent_messages_conversation", agent_messages.c.conversation_id, agent_messages.c.message_id)

agent_tool_activities = Table(
    "agent_tool_activities",
    metadata,
    Column("activity_id", IDENTITY_BIGINT, primary_key=True, autoincrement=True),
    Column(
        "conversation_id",
        Uuid(as_uuid=False),
        ForeignKey("agent_conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("tool_name", String(120), nullable=False),
    Column("arguments", JSON_DOCUMENT, nullable=False),
    Column("status", String(30), nullable=False),
    Column("summary", Text, nullable=False),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
)
Index("ix_agent_tool_activities_conversation", agent_tool_activities.c.conversation_id, agent_tool_activities.c.created_at)

agent_action_drafts = Table(
    "agent_action_drafts",
    metadata,
    Column("draft_id", Uuid(as_uuid=False), primary_key=True),
    Column("conversation_id", Uuid(as_uuid=False), nullable=False),
    Column("message_id", IDENTITY_BIGINT, nullable=False),
    Column("action_type", String(40), nullable=False),
    Column("target_code", String(160), nullable=False),
    Column("display_name", String(200), nullable=False),
    Column("category", String(120), nullable=False),
    Column("risk_level", String(30), nullable=False),
    Column("route", String(500), nullable=False),
    Column("objective", Text, nullable=False),
    Column("artifact_name", String(240)),
    Column("required_inputs", JSON_DOCUMENT, nullable=False),
    Column("stages", JSON_DOCUMENT, nullable=False),
    Column("approval_required", Boolean, nullable=False),
    Column("status", String(40), nullable=False),
    Column("created_at", UTC_TIMESTAMP, nullable=False),
    Column("preflight_status", String(50)),
    Column("preflight_checks", JSON_DOCUMENT, nullable=False, server_default=text("'[]'")),
    Column("preflight_at", UTC_TIMESTAMP),
    Column("input_schema", JSON_DOCUMENT, nullable=False, server_default=text("'[]'")),
    Column("input_values", JSON_DOCUMENT, nullable=False, server_default=text("'{}'")),
    ForeignKeyConstraint(
        ["conversation_id"],
        ["agent_conversations.conversation_id"],
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["message_id"],
        ["agent_messages.message_id"],
        ondelete="CASCADE",
    ),
)
Index("ix_agent_action_drafts_conversation", agent_action_drafts.c.conversation_id, agent_action_drafts.c.created_at)

agent_action_decisions = Table(
    "agent_action_decisions",
    metadata,
    Column("decision_id", Uuid(as_uuid=False), primary_key=True),
    Column("request_id", String(200), nullable=False, unique=True),
    Column("conversation_id", Uuid(as_uuid=False), nullable=False),
    Column("actor_user_id", IDENTITY_BIGINT, nullable=False),
    Column("actor_username", String(80), nullable=False),
    Column("operation_code", String(160), nullable=False),
    Column("artifact_name", String(240)),
    Column("decision", String(20), nullable=False),
    Column("payload_checksum", String(64), nullable=False),
    Column("payload_snapshot", JSON_DOCUMENT, nullable=False),
    Column("outcome_status", String(40), nullable=False),
    Column("execution_id", String(64)),
    Column("failure_summary", Text),
    Column("decided_at", UTC_TIMESTAMP, nullable=False),
    Column("finalized_at", UTC_TIMESTAMP),
    Column("completion_status", String(30)),
    Column("completion_message_id", IDENTITY_BIGINT),
    Column("completion_notified_at", UTC_TIMESTAMP),
    CheckConstraint(
        "decision IN ('APPROVE', 'REJECT')",
        name="decision",
    ),
    CheckConstraint(
        "outcome_status IN ('PROCESSING', 'SUBMITTED', 'APPROVED', "
        "'REJECTED', 'FAILED')",
        name="outcome",
    ),
    CheckConstraint(
        "completion_status IS NULL OR completion_status IN "
        "('SUCCESS', 'FAILED', 'RECOVERY_REQUIRED', 'CANCELLED')",
        name="completion_status",
    ),
)
Index(
    "ix_agent_action_decisions_conversation",
    agent_action_decisions.c.conversation_id,
    agent_action_decisions.c.decided_at.desc(),
)
Index(
    "ix_agent_action_decisions_execution",
    agent_action_decisions.c.execution_id,
)
