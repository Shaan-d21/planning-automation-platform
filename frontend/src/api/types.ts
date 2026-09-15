export interface ProductSummary {
  name: string;
  company: string;
  api_version: string;
}

export interface EnvironmentSummary {
  application_name: string;
  deployment_mode: string;
  base_url: string;
  configured: boolean;
  execution_account?: string;
}

export interface EnvironmentApplication {
  name: string;
  product_type: string | null;
  application_type: string | null;
  admin_mode: boolean | null;
}

export interface EnvironmentConfigurationResponse {
  status: string;
  base_url: string;
  deployment_mode: string;
  active_application: string | null;
  selected_application: string | null;
  selection_source: string | null;
  configured: boolean;
  restart_required: boolean;
  applications: EnvironmentApplication[];
  last_discovered_at: string | null;
  last_discovery_error: string | null;
  message: string | null;
}

export interface CurrentUser {
  user_id: number;
  username: string;
  display_name: string;
  email: string | null;
  platform_roles: string[];
  permissions: string[];
  persona: "SERVICE_ADMINISTRATOR" | "POWER_USER" | "USER" | "VIEWER";
  persona_label: string;
}

export interface NavigationItem {
  code: string;
  label: string;
  path: string;
  group: string;
}

export interface FeatureAvailability {
  legacy_ui: boolean;
  task_engine: boolean;
  planning_cycles: boolean;
  approvals: boolean;
  notifications: boolean;
  access_control: boolean;
  jobs_activity: boolean;
}

export interface BootstrapResponse {
  product: ProductSummary;
  authenticated: boolean;
  requires_bootstrap: boolean;
  csrf_token: string;
  identity_authentication: {
    federated_enabled: boolean;
    oracle_credentials_enabled: boolean;
    provider_name: string;
    login_url: string | null;
    local_recovery_enabled: boolean;
  };
  environment: EnvironmentSummary | null;
  user: CurrentUser | null;
  navigation: NavigationItem[];
  features: FeatureAvailability;
}

export interface InitialAdministratorInput {
  username: string;
  display_name: string;
  email: string | null;
  password: string;
  password_confirmation: string;
}

export type TaskStatus =
  | "NOT_STARTED"
  | "IN_PROGRESS"
  | "BLOCKED"
  | "COMPLETED"
  | "CANCELLED";

export type TaskReadiness =
  | "READY"
  | "WAITING"
  | "BLOCKED"
  | "COMPLETED"
  | "CANCELLED";

export interface PlanningStage {
  stage_id: number;
  cycle_id: number;
  sequence: number;
  code: string;
  name: string;
  status: string;
  start_date: string | null;
  due_date: string | null;
  completed_at: string | null;
}

export interface PlanningCycle {
  cycle_id: number;
  code: string;
  name: string;
  cycle_type: string;
  process_code: string | null;
  scenario: string | null;
  year: string;
  actual_through_period: string | null;
  forecast_start_period: string | null;
  start_date: string;
  due_date: string;
  status: string;
  completed_at: string | null;
  completed_stages: number;
  stage_count: number;
  progress_percent: number;
  current_stage: PlanningStage | null;
  stages: PlanningStage[];
}

export interface PlanningTask {
  task_id: number;
  stage_id: number;
  cycle_id: number;
  cycle_code: string;
  cycle_name: string;
  stage_code: string;
  stage_name: string;
  title: string;
  description: string;
  task_type: string;
  status: TaskStatus;
  readiness: TaskReadiness;
  priority: "LOW" | "NORMAL" | "HIGH" | "CRITICAL";
  assigned_user_id: number | null;
  assigned_role_code: string | null;
  entity: string | null;
  scenario: string | null;
  period: string | null;
  due_at: string | null;
  action_type: string;
  action_config: Record<string, unknown>;
  dependency_ids: number[];
  incomplete_dependency_ids: number[];
  completed_at: string | null;
  execution_attempts: PlanningTaskExecution[];
  latest_validation?: PlanningValidationEvidence | null;
}

export interface PlanningTaskExecution {
  task_execution_id: number;
  execution_id: string;
  attempt_number: number;
  status: "QUEUED" | "RUNNING" | "SUCCESS" | "FAILED";
  linked_at: string;
  updated_at: string;
  completed_at: string | null;
  error_message: string | null;
  run_url: string;
}

export interface RecentActivity {
  execution_id: string;
  name: string;
  status: string;
  started_at: string;
  initiated_by: string;
  trigger_source: string;
}

export interface HomeResponse {
  status: string;
  summary: {
    action_required: number;
    due_today: number;
    overdue: number;
    completed: number;
  };
  cycles: PlanningCycle[];
  tasks: PlanningTask[];
  recent_activity: RecentActivity[];
}

export interface PlanningWorkResponse {
  status: string;
  cycles: PlanningCycle[];
  tasks: PlanningTask[];
}

export interface PlanningCycleAssigneeUser {
  user_id: number;
  username: string;
  display_name: string;
  roles: string[];
}

export interface PlanningCycleAssigneeRole {
  code: string;
  name: string;
  description: string;
}

export interface PlanningCycleAdministrationResponse {
  status: string;
  cycles: PlanningCycle[];
  users: PlanningCycleAssigneeUser[];
  roles: PlanningCycleAssigneeRole[];
}

export interface PlanningCycleStageInput {
  code: string;
  name: string;
  sequence: number;
  start_date: string | null;
  due_date: string | null;
}

export interface PlanningCycleTaskInput {
  key: string;
  stage_code: string;
  title: string;
  description: string;
  task_type: string;
  priority: "LOW" | "NORMAL" | "HIGH" | "CRITICAL";
  assigned_username: string | null;
  assigned_role_code: string | null;
  entity: string | null;
  scenario: string | null;
  period: string | null;
  due_at: string | null;
  action_type: string;
  action_config: Record<string, unknown>;
  depends_on: string[];
}

export interface PlanningCycleCreateInput {
  code: string;
  name: string;
  cycle_type: string;
  process_code: string | null;
  scenario: string | null;
  year: string;
  actual_through_period: string | null;
  forecast_start_period: string | null;
  start_date: string;
  due_date: string;
  stages: PlanningCycleStageInput[];
  tasks: PlanningCycleTaskInput[];
}

export interface PlanningApproval {
  approval_id: number;
  cycle_id: number;
  cycle_name: string;
  submitted_task_id: number;
  submitted_task_title: string;
  approval_task_id: number;
  approval_task_title: string;
  entity: string | null;
  scenario: string | null;
  period: string | null;
  status: "PENDING" | "APPROVED" | "RETURNED" | "CANCELLED";
  submitted_by_user_id: number;
  submitted_by_name: string;
  submitted_at: string;
  decided_by_user_id: number | null;
  decided_at: string | null;
  decision_comment: string | null;
  validation?: PlanningValidationEvidence | null;
}

export interface PlanningValidationEvidence {
  validation_id: number;
  task_id: number;
  validation_type: "QUALITY" | "COMPARISON";
  status: "PASS" | "WARNING" | "FAIL";
  source_cube: string;
  target_cube: string | null;
  selection: DataReviewSliceInput;
  criteria: Record<string, unknown>;
  checked_cells: number;
  matched_cells: number | null;
  exception_count: number;
  warning_count: number;
  performed_by_user_id: number;
  performed_at: string;
  warning_acknowledged_by_user_id: number | null;
  warning_acknowledged_at: string | null;
  completion_allowed: boolean;
}

export interface DataReviewTaskContextResponse {
  status: string;
  task: PlanningTask;
  configuration: {
    slice?: DataReviewSliceInput;
    validation_type?: "QUALITY" | "COMPARISON";
    rules?: Partial<DataQualityRulesInput>;
    target_cube?: string;
    tolerance?: number;
  } | null;
  suggested_slice: DataReviewSliceInput | null;
}

export interface PlanningApprovalsResponse {
  status: string;
  approvals: PlanningApproval[];
}

export interface UserNotification {
  notification_id: number;
  event_type: string;
  severity: "INFO" | "SUCCESS" | "WARNING" | "ERROR";
  title: string;
  message: string;
  action_url: string | null;
  source_type: string | null;
  source_id: string | null;
  created_at: string;
  read_at: string | null;
}

export interface NotificationsResponse {
  status: string;
  unread_count: number;
  notifications: UserNotification[];
}

export type PlatformRoleCode =
  | "SERVICE_ADMINISTRATOR"
  | "POWER_USER"
  | "USER"
  | "VIEWER";

export interface PlatformRole {
  code: PlatformRoleCode;
  name: string;
  description: string;
  permissions: string[];
}

export interface PlatformUser {
  user_id: number;
  username: string;
  display_name: string;
  email: string | null;
  active: boolean;
  role_code: PlatformRoleCode;
  created_at: string;
  updated_at: string;
  last_login_at: string | null;
  authentication_source: "LOCAL_RECOVERY" | "ORACLE_LINKED" | "ORACLE_AND_LOCAL";
}

export interface AccessControlResponse {
  status: string;
  current_user_id: number;
  users: PlatformUser[];
  roles: PlatformRole[];
  identity_sync: IdentitySyncStatus;
}

export interface IdentitySyncStatus {
  available: boolean;
  provider_code: string;
  provider_name: string;
  provider_registered: boolean;
  identity_provider_mode: string;
  sso_enabled: boolean;
  oracle_password_login_enabled: boolean;
  synced_identities: number;
  active_identities: number;
  mapped_entitlements: number;
  message: string;
}

export type IdentityPreviewAction = "ADD" | "UPDATE" | "UNCHANGED" | "DEACTIVATE";

export interface IdentityPreviewEntry {
  subject: string;
  username: string;
  display_name: string;
  email: string | null;
  active: boolean;
  action: IdentityPreviewAction;
  application_roles: string[];
  granular_roles: string[];
  groups: string[];
}

export interface IdentitySyncPreview {
  provider: { code: string; display_name: string; provider_type: string };
  snapshot_checksum: string;
  retrieved_at: string;
  complete: boolean;
  summary: {
    total_seen: number;
    additions: number;
    updates: number;
    unchanged: number;
    deactivations: number;
  };
  warnings: string[];
  identities: IdentityPreviewEntry[];
}

export interface IdentitySyncPreviewResponse {
  status: string;
  preview: IdentitySyncPreview;
}

export interface IdentitySyncApplyResponse {
  status: string;
  message: string;
  result: {
    sync_run_id: number;
    sync_status: string;
    identities_seen: number;
    identities_linked: number;
    identities_deactivated: number;
    entitlements_seen: number;
    completed_at: string;
  };
  applied_preview: IdentitySyncPreview;
}

export interface IdentityEntitlement {
  entitlement_id: number;
  entitlement_type: "APPLICATION_ROLE" | "GRANULAR_ROLE" | "GROUP";
  external_key: string;
  display_name: string;
  active: boolean;
  assigned_identity_count: number;
  mapped_role: PlatformRoleCode | null;
  mapping_enabled: boolean;
}

export interface IdentityMappingCatalogResponse {
  status: string;
  provider_code: string;
  entitlements: IdentityEntitlement[];
}

export type IdentityProvisioningAction = "CREATE" | "UPDATE" | "UNCHANGED" | "DEACTIVATE" | "SKIP_UNMAPPED" | "CONFLICT";

export interface IdentityProvisioningEntry {
  external_identity_id: number;
  user_id: number | null;
  username: string;
  display_name: string;
  email: string | null;
  target_role: PlatformRoleCode | null;
  action: IdentityProvisioningAction;
  matched_entitlements: string[];
  explanation: string;
}

export interface IdentityProvisioningPreview {
  provider_code: string;
  checksum: string;
  generated_at: string;
  summary: {
    creates: number;
    updates: number;
    unchanged: number;
    deactivations: number;
    unmapped: number;
    conflicts: number;
  };
  entries: IdentityProvisioningEntry[];
}

export interface IdentityProvisioningPreviewResponse {
  status: string;
  preview: IdentityProvisioningPreview;
}

export interface IdentityProvisioningApplyResponse {
  status: string;
  message: string;
  result: {
    created: number;
    updated: number;
    deactivated: number;
    unchanged: number;
    unmapped: number;
    conflicts: number;
  };
  applied_preview: IdentityProvisioningPreview;
}

export type JobStatus = "QUEUED" | "RUNNING" | "SUCCESS" | "FAILED";
export type JobStepStatus = "PENDING" | "RUNNING" | "SUCCESS" | "FAILED" | "SKIPPED";

export interface JobActivitySummary {
  execution_id: string;
  name: string;
  status: JobStatus;
  started_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
  completed_steps: number;
  total_steps: number;
  initiated_by: string;
  trigger_source: string;
  executed_by?: string | null;
  error_message: string | null;
}

export interface JobActivityStep {
  sequence: number;
  name: string;
  status: JobStepStatus;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  details: Record<string, unknown>;
  error_message: string | null;
}

export interface JobActivityDetail extends JobActivitySummary {
  steps: JobActivityStep[];
  record_statistics: OracleRecordStatistics | null;
  lineage: JobLoadLineage | null;
  oracle_messages: OracleJobMessage[];
  rejected_records: RejectedRecordSet[];
  artifacts: JobArtifact[];
  notices: string[];
}

export interface JobLoadLineage {
  operation?: string | null;
  source_kind?: string | null;
  source_file?: string | null;
  staging_location?: string | null;
  oracle_job_name?: string | null;
  target_application?: string | null;
  target_system?: string | null;
  origin_note?: string | null;
}

export interface OracleJobMessage {
  message_type: string;
  category: string | null;
  message: string;
  dimension_name: string | null;
  child_job_id: string | null;
}

export interface RejectedRecordSet {
  file_name: string;
  dimension_name: string | null;
  columns: string[];
  rows: string[][];
  preview_count: number;
  truncated: boolean;
}

export interface JobArtifact {
  artifact_id: string;
  name: string;
  kind: string;
  size_bytes: number;
  download_url: string;
}

export interface JobsActivityResponse {
  status: string;
  summary: {
    total: number;
    running: number;
    successful: number;
    failed: number;
    success_rate: number;
  };
  jobs: JobActivitySummary[];
}

export type ScheduleFrequency = "ONE_TIME" | "DAILY" | "WEEKLY" | "MONTHLY";
export type AutomationTargetType = "ORACLE_PIPELINE" | "RTP_REGISTRY_SYNC";
export type ScheduleRunOutcome = "NEVER" | "CLAIMED" | "SUBMITTED" | "COMPLETED" | "FAILED" | "SKIPPED";

export type AutomationInputPolicy = "ORACLE_DEFAULTS" | "FIXED";
export type AutomationMisfirePolicy = "RUN_ONCE" | "SKIP";

export interface AutomationSchedule {
  schedule_id: number;
  name: string;
  target_type: AutomationTargetType;
  target_key: string;
  frequency: ScheduleFrequency;
  timezone: string;
  first_run_local: string;
  input_policy: AutomationInputPolicy;
  variables: Record<string, string>;
  inbox_files: Record<string, string>;
  misfire_policy: AutomationMisfirePolicy;
  concurrency_policy: "SKIP_IF_ACTIVE";
  enabled: boolean;
  next_run_at: string | null;
  created_at: string;
  updated_at: string;
  last_triggered_at: string | null;
  last_execution_id: string | null;
  last_outcome: ScheduleRunOutcome;
  last_error: string | null;
}

export interface AutomationSchedulesResponse {
  status: string;
  schedules: AutomationSchedule[];
}

export type AutomationScheduleRunStatus = "CLAIMED" | "SUBMITTED" | "COMPLETED" | "FAILED" | "SKIPPED";

export interface AutomationScheduleRunEvidence {
  run_id: number;
  schedule_id: number;
  schedule_name: string;
  target_type: AutomationTargetType;
  target_key: string;
  scheduled_for: string;
  claimed_at: string;
  completed_at: string | null;
  status: AutomationScheduleRunStatus;
  execution_id: string | null;
  error_message: string | null;
}

export interface AutomationScheduleRunsResponse {
  status: string;
  summary: {
    total: number;
    submitted: number;
    completed: number;
    failed: number;
    skipped: number;
    claimed: number;
  };
  runs: AutomationScheduleRunEvidence[];
}

export interface AutomationScheduleInput {
  name: string;
  target_type: AutomationTargetType;
  target_key: string;
  frequency: ScheduleFrequency;
  timezone: string;
  first_run_local: string;
  input_policy: AutomationInputPolicy;
  variables: Record<string, string>;
  inbox_files: Record<string, string>;
  misfire_policy: AutomationMisfirePolicy;
  enabled: boolean;
}

export interface SchedulePreviewResponse {
  status: string;
  next_run_at: string;
  next_run_local: string;
  message: string;
}

export interface ScheduleMutationResponse {
  status: string;
  message: string;
  schedule: AutomationSchedule;
}

export interface OperationSummary {
  code: string;
  display_name: string;
  description: string;
  category: string;
  risk_level: "Read only" | "Controlled" | "Elevated" | string;
  route: string;
}

export interface OperationsResponse {
  status: string;
  operations: OperationSummary[];
}

export interface OperationArtifactCatalog {
  status: string;
  jobs: string[];
}

export interface BusinessRuleCatalogResponse extends OperationArtifactCatalog {
  rtp_registry?: BusinessRuleRTPRegistryStatus;
}

export type OracleFilePurpose = "data-import" | "metadata-import" | "data-integration" | "pipeline";

export interface OracleRepositoryFile {
  name: string;
  folder: string;
  file_type: string;
  size_bytes: number | null;
  last_modified_epoch_ms: number | null;
}

export interface OracleFileCatalogResponse {
  status: string;
  purpose: OracleFilePurpose;
  files: OracleRepositoryFile[];
}

export interface MetadataImportCatalogResponse extends OperationArtifactCatalog {
  refresh_jobs: string[];
}

export interface BusinessRuleRunInput {
  rule_name: string;
  runtime_prompts: Record<string, string>;
  planning_task_id?: number | null;
}

export interface RuntimePromptDefinition {
  name: string;
  label: string;
  order: number;
  value_type: string;
  dimension: string | null;
  default_value: string | null;
  has_default: boolean;
  required: boolean;
  hidden: boolean;
  allow_multiple: boolean;
  security_mode: string | null;
  scope_type: string;
  scope_name: string | null;
  source_variable_id: string | null;
  limit_type: string | null;
  limit_value: string | null;
}

export interface BusinessRuleRTPDefinition {
  rule_name: string;
  cube_name: string | null;
  source_name: string;
  source_checksum: string;
  parser_version: string;
  definition_checksum: string;
  synchronized_at: string;
  prompts: RuntimePromptDefinition[];
}

export interface BusinessRuleRTPImportResult {
  sync_run_id: number;
  source_name: string;
  source_checksum: string;
  parser_version: string;
  rules_imported: number;
  prompts_imported: number;
  warnings: string[];
  completed_at: string;
  rules_added?: number;
  rules_changed?: number;
  rules_unchanged?: number;
}

export interface BusinessRuleRTPDefinitionResponse {
  status: string;
  definition: BusinessRuleRTPDefinition | null;
  fallback: boolean;
  message: string;
  latest_import: BusinessRuleRTPImportResult | null;
}

export interface BusinessRuleRTPImportResponse {
  status: string;
  message: string;
  result: BusinessRuleRTPImportResult;
}

export interface BusinessRuleRTPRegistryDefinitionStatus {
  rule_name: string;
  cube_name: string | null;
  source_name: string;
  synchronized_at: string;
  prompt_count: number;
  required_prompt_count: number;
  live_status: "SYNCHRONIZED" | "NOT_IN_LIVE_CATALOG" | "CATALOG_UNAVAILABLE" | string;
}

export interface BusinessRuleRTPRegistrySyncRun {
  sync_run_id: number;
  source_name: string;
  parser_version: string;
  status: "RUNNING" | "COMPLETED" | "FAILED" | string;
  rules_imported: number;
  prompts_imported: number;
  warnings: string[];
  error_summary: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface BusinessRuleRTPRegistryStatus {
  health: "HEALTHY" | "ATTENTION" | "EMPTY" | "CATALOG_UNAVAILABLE" | string;
  application_name: string;
  live_catalog_available: boolean;
  live_rule_count: number | null;
  synchronized_rule_count: number;
  synchronized_prompt_count: number;
  unsynchronized_live_rules: string[];
  definitions_not_in_live_catalog: string[];
  definitions: BusinessRuleRTPRegistryDefinitionStatus[];
  recent_syncs: BusinessRuleRTPRegistrySyncRun[];
}

export interface BusinessRuleRTPRegistryStatusResponse {
  status: string;
  registry: BusinessRuleRTPRegistryStatus;
  live_catalog_error: string | null;
}

export interface DataMapRunInput {
  data_map_name: string;
  clear_target: boolean;
  member_overrides: Record<string, string>;
  exclusion_overrides: Record<string, string>;
  planning_task_id?: number | null;
}

export interface DataIntegrationDefinition {
  name: string;
  description: string | null;
}

export type OracleArtifactStatus = "VERIFIED" | "PENDING" | "MISSING" | "UNAVAILABLE" | "INACTIVE";

export interface OracleArtifactRegistration {
  artifact_id: number;
  artifact_type: "PIPELINE" | "DATA_INTEGRATION" | "BUSINESS_RULE" | "DATA_MAP" | "METADATA_IMPORT_JOB" | "DATA_IMPORT_JOB" | "CUBE_REFRESH_JOB" | "CUBE";
  oracle_identifier: string;
  display_name: string;
  description: string | null;
  source: "SEED" | "MANUAL" | "PIPELINE_DISCOVERY" | "LIVE_DISCOVERY" | "LEGACY";
  status: OracleArtifactStatus;
  is_active: boolean;
  is_runnable: boolean;
  is_verified: boolean;
  consecutive_missing_count: number;
  last_verified_at: string | null;
  last_error: string | null;
}

export interface DataIntegrationCatalogResponse {
  status: string;
  integrations: DataIntegrationDefinition[];
  artifacts: OracleArtifactRegistration[];
}

export interface DataIntegrationRegistrationResponse {
  status: string;
  message: string;
  integration: OracleArtifactRegistration;
}

export interface DataIntegrationRunInput {
  integration_name: string;
  start_period: string;
  end_period: string;
  import_mode: string;
  export_mode: string;
  upload_token: string | null;
  upload_target?: string | null;
  inbox_file: string | null;
  use_configured_file?: boolean;
  planning_task_id?: number | null;
}

export interface DataImportRunInput {
  job_name: string;
  upload_token: string | null;
  inbox_file: string | null;
  use_configured_file?: boolean;
  error_file_name: string | null;
  planning_task_id?: number | null;
}

export interface MetadataImportRunInput extends DataImportRunInput {
  refresh_job_name: string | null;
}

export interface CubeRefreshRunInput {
  job_name: string;
  planning_task_id?: number | null;
}

export interface SubstitutionVariableDefinition {
  name: string;
  value: string;
  scope: string;
}

export interface SubstitutionVariablePlanType {
  name: string;
  cube_name: string;
  identifier: number;
  cube_type: number;
  dimension_count: number;
}

export interface SubstitutionVariableCatalogResponse {
  status: string;
  catalog: {
    variables: SubstitutionVariableDefinition[];
    plan_types: SubstitutionVariablePlanType[];
    scopes: string[];
  };
}

export interface SubstitutionVariableRunInput {
  action: "UPDATE" | "CREATE";
  scope: string;
  name: string;
  value: string;
  expected_current_value: string | null;
  planning_task_id?: number | null;
}

export interface UserVariableDefinition {
  name: string;
  dimension: string;
}

export interface UserVariableValue {
  user_name: string;
  name: string;
  dimension: string;
  member: string;
}

export interface UserVariableCatalogResponse {
  status: string;
  catalog: {
    user_name: string;
    definitions: UserVariableDefinition[];
    values: UserVariableValue[];
  };
}

export interface UserVariableRunInput {
  user_name: string;
  name: string;
  dimension: string;
  member: string;
  expected_current_member: string | null;
  planning_task_id?: number | null;
}

export interface ReportCatalogItem {
  name: string;
  title: string;
  cube: string;
  default_pov: [string, string][];
  rows: [string, string[]][];
  columns: [string, string[]][];
}

export interface ReportCatalogResponse {
  status: string;
  reports: ReportCatalogItem[];
}

export interface ReportPreflight {
  form_name: string;
  title: string;
  cube: string | null;
  registered: boolean;
  page_dimensions: string[];
  row_dimensions: string[];
  column_dimensions: string[];
  current_pov: [string, string][];
  allowed_page_members: [string, string[]][];
}

export interface ReportPreflightResponse {
  status: string;
  preflight: ReportPreflight;
}

export interface ReportAxisDimensionInput {
  dimension: string;
  members: string[];
}

export interface ReportRegistrationInput {
  name: string;
  title: string;
  cube: string;
  pov: Record<string, string>;
  columns: ReportAxisDimensionInput[];
  rows: ReportAxisDimensionInput[];
}

export interface ReportRegistrationResponse {
  status: string;
  message: string;
  report: ReportCatalogItem;
}

export interface DataExplorerViewsResponse {
  status: string;
  views: ReportCatalogItem[];
}

export interface DataExplorerViewResponse {
  status: string;
  message: string;
  view: ReportCatalogItem;
}

export interface ReportRunInput {
  form_name: string;
  title: string;
  page_member_overrides: Record<string, string>;
}

export interface PipelineDefinition {
  code: string;
  name: string;
  description: string | null;
}

export interface PipelineCatalogResponse {
  status: string;
  pipelines: PipelineDefinition[];
  artifacts: OracleArtifactRegistration[];
}

export interface OracleArtifactSyncResponse {
  status: string;
  sync: {
    environment_key: string;
    application_name: string;
    oracle_available: boolean;
    verified_pipelines: number;
    missing_pipelines: number;
    discovered_integrations: number;
    verified_integrations: number;
    missing_integrations: number;
    pending_integrations: number;
    verification_errors: number;
    verified_business_rules: number;
    verified_data_maps: number;
    verified_metadata_jobs: number;
    verified_data_import_jobs: number;
    verified_cube_refresh_jobs: number;
    verified_cubes: number;
    total_verified: number;
    total_hidden: number;
    synchronized_at: string | null;
    message: string;
  };
  artifacts: OracleArtifactRegistration[];
}

export interface OracleCatalogResponse {
  status: string;
  environment: {
    application_name: string;
    deployment_mode: string;
  };
  summary: {
    verified: number;
    attention: number;
    last_synchronized_at: string | null;
  };
  artifacts: OracleArtifactRegistration[];
}

export interface PipelineVariablePreview {
  name: string;
  display_name: string;
  default_value: string | null;
  required: boolean;
  editable: boolean;
}

export interface PipelineFilePreview {
  key: string;
  display_name: string;
  configured_reference: string | null;
  required: boolean;
  allowed_extensions: string[];
  consumers: string[];
}

export interface PipelineStagePreview {
  name: string;
  display_name: string;
  job_count: number;
  runs_in_parallel: boolean;
}

export interface PipelineOperationPreview {
  code: string;
  display_name: string;
  variables: PipelineVariablePreview[];
  file_requirements: PipelineFilePreview[];
  stages: PipelineStagePreview[];
}

export interface PipelinePreflightResponse {
  status: string;
  preview: PipelineOperationPreview;
}

export interface PipelineRegistrationResponse extends PipelinePreflightResponse {
  message: string;
  pipeline: PipelineDefinition;
}

export interface PipelineRunInput {
  pipeline_code: string;
  variables: Record<string, string>;
  uploads: Record<string, string>;
  inbox_files: Record<string, string>;
  planning_task_id?: number | null;
}

export interface UploadReceiptResponse {
  status: string;
  upload: {
    token: string;
    filename: string;
    size: number;
  };
}

export interface OperationAcceptedResponse {
  status: "accepted";
  execution_id: string;
  redirect: string;
}

export interface OperationExecutionStep {
  name: string;
  sequence: number;
  status: "PENDING" | "RUNNING" | "SUCCESS" | "FAILED" | "SKIPPED";
  started_at: string | null;
  completed_at: string | null;
  details: Record<string, unknown>;
  error_message: string | null;
}

export interface StandaloneFlowExecutionStep {
  sequence: number;
  operation_code: string;
  display_name: string;
  artifact_name: string;
  status: "PENDING" | "RUNNING" | "SUCCESS" | "FAILED" | "SKIPPED";
  started_at: string | null;
  completed_at: string | null;
  child_execution_id: string | null;
  child_status: string | null;
  active_stage: string | null;
  oracle_job_id: string | number | null;
  oracle_status: string | null;
  record_statistics: OracleRecordStatistics | null;
  error_message: string | null;
}

export interface StandaloneFlowProgress {
  current_step: StandaloneFlowExecutionStep | null;
  completed_steps: number;
  successful_steps: number;
  total_steps: number;
  progress_percent: number;
  steps: StandaloneFlowExecutionStep[];
  record_statistics: OracleRecordStatistics | null;
  recovery: {
    source_execution_id: string;
    from_original_step: number;
  } | null;
}

export interface StandaloneFlowRecoveryStep {
  sequence: number;
  operation_code: string;
  display_name: string;
  artifact_name: string;
  original_status: "FAILED" | "SKIPPED";
}

export interface StandaloneFlowRecoveryUpload {
  key: string;
  step_sequence: number;
  label: string;
  original_filename: string;
  allowed_extensions: string[];
}

export interface StandaloneFlowRecoveryPlan {
  source_execution_id: string;
  flow_name: string;
  failed_step_sequence: number;
  failure_reason: string;
  retryable: boolean;
  blocked_reason: string | null;
  steps: StandaloneFlowRecoveryStep[];
  required_uploads: StandaloneFlowRecoveryUpload[];
}

export interface StandaloneFlowRecoveryResponse {
  status: string;
  recovery: StandaloneFlowRecoveryPlan;
}

export interface StandaloneFlowRecoveryAccepted extends OperationAcceptedResponse {
  source_execution_id: string;
}

export interface OperationExecution {
  execution_id: string;
  operation_name: string;
  status: "QUEUED" | "RUNNING" | "SUCCESS" | "FAILED" | "RECOVERY_REQUIRED";
  started_at: string;
  completed_at: string | null;
  error_message: string | null;
  initiated_by: string | null;
  trigger_source: string | null;
  executed_by?: string | null;
  steps: OperationExecutionStep[];
  completed_steps: number;
  total_steps: number;
  artifacts: { name: string; url: string }[];
  record_statistics: OracleRecordStatistics | null;
  flow_progress: StandaloneFlowProgress | null;
  log_url: string | null;
  terminal: boolean;
}

export interface OracleRecordStatisticsDetail {
  dimension_name: string | null;
  load_type: string | null;
  records_read: number;
  records_processed: number;
  records_rejected: number;
}

export interface OracleRecordStatistics {
  source: "ORACLE_JOB_DETAILS" | "ORACLE_DATA_INTEGRATION_STATUS" | "ORACLE_DATA_INTEGRATION_LOG" | "ORACLE_COMBINED_EVIDENCE";
  records_read: number;
  records_processed: number;
  records_rejected: number;
  details: OracleRecordStatisticsDetail[];
}

export interface DataReviewCube {
  name: string;
  cube_name: string;
  cube_type: number | null;
  dimension_count: number | null;
}

export interface DataReviewDimension {
  name: string;
  dimension_type: string | null;
}

export interface DataReviewMember {
  name: string;
  alias: string | null;
  path: string | null;
  parent_name: string | null;
  has_children: boolean;
}

export interface DataReviewAxisInput {
  dimension: string;
  members: string[];
}

export interface DataReviewSliceInput {
  cube: string;
  pov: Record<string, string>;
  rows: DataReviewAxisInput[];
  columns: DataReviewAxisInput[];
}

export interface DataReviewGridRow {
  headers: string[];
  data: unknown[];
}

export interface DataReviewGrid {
  row_dimensions: string[];
  column_dimensions: string[];
  columns: string[][];
  rows: DataReviewGridRow[];
  pov: [string, string][];
}

export interface DataReviewResult {
  cube: string;
  form_name: string;
  grid: DataReviewGrid;
  row_count: number;
  column_count: number;
  cell_count: number;
  missing_cell_count: number;
}

export interface DataReviewCubesResponse {
  status: string;
  cubes: DataReviewCube[];
}

export interface DataReviewDimensionsResponse {
  status: string;
  cube: string;
  dimensions: DataReviewDimension[];
}

export interface DataReviewMembersResponse {
  status: string;
  cube: string;
  dimension: string;
  query: string;
  members: DataReviewMember[];
  total_matches: number;
  has_more: boolean;
  offset: number;
  limit: number;
}

export interface DataReviewGridResponse {
  status: string;
  review: DataReviewResult;
}

export interface AgentStatusResponse {
  enabled: boolean;
  configured: boolean;
  provider: string;
  model: string;
  mode: string;
  message: string;
}

export interface AgentConversation {
  conversation_id: string;
  user_id: number;
  title: string;
  provider: string;
  model: string;
  created_at: string;
  updated_at: string;
}

export interface AgentMessage {
  message_id: number;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface AgentToolActivity {
  name: string;
  arguments: Record<string, unknown>;
  status: string;
  summary: string;
  result?: Record<string, unknown> | null;
}

export interface AgentApprovalRequest {
  request_id: string;
  operation_code: string;
  display_name: string;
  objective: string;
  artifact_name: string | null;
  category: string;
  risk_level: string;
  route: string;
  effect: string;
  input_values: Record<string, unknown>;
}

export interface AgentActionDecision {
  decision_id: string;
  request_id: string;
  conversation_id: string;
  actor_user_id: number;
  actor_username: string;
  operation_code: string;
  artifact_name: string | null;
  decision: "APPROVE" | "REJECT" | string;
  payload_checksum: string;
  payload_snapshot: Record<string, unknown>;
  outcome_status: "PROCESSING" | "SUBMITTED" | "APPROVED" | "REJECTED" | "FAILED" | string;
  execution_id: string | null;
  failure_summary: string | null;
  decided_at: string;
  finalized_at: string | null;
}

export interface AgentClarificationRequest {
  request_id: string;
  operation_code: string;
  display_name: string;
  prompt: string;
  options: string[];
  allows_cancel: boolean;
  recommendations: AgentArtifactRecommendation[];
  option_labels: Record<string, string>;
  catalog_recovery?: AgentCatalogRecovery;
  search_context?: string;
}

export interface AgentCatalogRecovery {
  enabled: boolean;
  can_manage?: boolean;
  identifier_label: string;
  identifier_placeholder: string;
  registration_mode: "verified" | "pending" | string;
  help: string;
}

export interface AgentArtifactRecommendation {
  name: string;
  display_name?: string;
  confidence: "Strong match" | "Possible match" | string;
  score: number;
  reason: string;
}

export interface AgentInputRequest {
  request_id: string;
  operation_code: string;
  display_name: string;
  artifact_name: string;
  title: string;
  description: string;
  fields: AgentActionInputField[];
  context: Record<string, unknown>;
}

export interface AgentDraftCheck {
  code: string;
  label: string;
  status: string;
  message: string;
}

export interface AgentActionInputField {
  key: string;
  label: string;
  kind: "text" | "boolean" | "choice" | "key_value" | "file_reference" | "pipeline_review" | "pipeline_variable" | "pipeline_file" | "schedule";
  required: boolean;
  description: string;
  placeholder: string;
  options: string[];
  name?: string;
  default_value?: string | null;
  editable?: boolean;
  file_key?: string;
  configured_reference?: string | null;
  allowed_extensions?: string[];
  consumers?: string[];
}

export interface AgentActionDraft {
  draft_id: string;
  conversation_id: string;
  message_id: number;
  action_type: string;
  target_code: string;
  display_name: string;
  category: string;
  risk_level: string;
  route: string;
  objective: string;
  artifact_name: string | null;
  required_inputs: string[];
  stages: string[];
  approval_required: boolean;
  status: string;
  created_at: string;
  input_schema: AgentActionInputField[];
  input_values: Record<string, unknown>;
  preflight_status: string | null;
  preflight_checks: AgentDraftCheck[];
  preflight_at: string | null;
}

export interface AgentConversationsResponse {
  status: string;
  conversations: AgentConversation[];
}

export interface AgentMessagesResponse {
  status: string;
  messages: AgentMessage[];
  action_drafts: AgentActionDraft[];
  approval_request: AgentApprovalRequest | null;
  clarification_request: AgentClarificationRequest | null;
  input_request: AgentInputRequest | null;
  data_review_context?: AgentDataReviewContext | null;
}

export interface AgentDataReviewContext {
  tool: "review_data_slice" | "review_saved_data_view" | "compare_data_slices" | string;
  selection: Record<string, unknown>;
}

export interface AgentSendResponse {
  status: string;
  message: AgentMessage;
  tool_activity: AgentToolActivity[];
  action_drafts: AgentActionDraft[];
  approval_request: AgentApprovalRequest | null;
  clarification_request: AgentClarificationRequest | null;
  input_request: AgentInputRequest | null;
  execution?: AgentApprovedExecution | null;
  schedule?: AutomationSchedule | null;
  decision?: AgentActionDecision | null;
}

export interface AgentClarificationRefreshResponse {
  status: string;
  message: string;
  clarification_request: AgentClarificationRequest;
}

export interface AgentApprovedExecution {
  execution_id: string;
  operation_code: string;
  target_name: string;
  status: string;
}

export interface DataQualityRulesInput {
  check_missing: boolean;
  check_zero: boolean;
  minimum: number | null;
  maximum: number | null;
  max_issues: number;
}

export interface DataReviewValidationInput {
  slice: DataReviewSliceInput;
  rules: DataQualityRulesInput;
  planning_task_id?: number | null;
}

export interface DataQualityIssue {
  code: "MISSING" | "ZERO" | "BELOW_MINIMUM" | "ABOVE_MAXIMUM" | "NON_NUMERIC";
  severity: "ERROR" | "WARNING";
  message: string;
  pov: [string, string][];
  row_headers: string[];
  column_headers: string[];
  raw_value: unknown;
  numeric_value: number | string | null;
}

export interface DataQualityResult {
  status: "PASS" | "WARNING" | "FAIL";
  checked_cells: number;
  passed_cells: number;
  issue_count: number;
  missing_count: number;
  zero_count: number;
  below_minimum_count: number;
  above_maximum_count: number;
  non_numeric_count: number;
  issues: DataQualityIssue[];
  truncated: boolean;
  rules: DataQualityRulesInput;
}

export interface DataReviewValidationResponse {
  status: string;
  validation: {
    cube: string;
    form_name: string;
    result: DataQualityResult;
  };
  task_validation?: PlanningValidationEvidence | null;
}

export interface DataComparisonCell {
  row_headers: string[];
  column_headers: string[];
  source_value: number | string | null;
  target_value: number | string | null;
  difference: number | string | null;
  matches: boolean;
}

export interface DataComparisonMismatch {
  row_headers: string[];
  column_headers: string[];
  source_value: number | string | null;
  target_value: number | string | null;
  difference: number | string | null;
}

export interface DataReviewComparisonInput {
  source: DataReviewSliceInput;
  target: DataReviewSliceInput;
  tolerance: number;
  max_mismatches: number;
  include_cells: boolean;
  planning_task_id?: number | null;
}

export interface DataReviewComparisonResponse {
  status: string;
  comparison: {
    source_cube: string;
    target_cube: string;
    result: {
      source_form: string;
      target_form: string;
      compared_cells: number;
      matched_cells: number;
      mismatches: DataComparisonMismatch[];
      tolerance: number | string;
      cells: DataComparisonCell[];
    };
  };
  task_validation?: PlanningValidationEvidence | null;
}

export interface PlatformUserCreateInput {
  username: string;
  display_name: string;
  email: string | null;
  password: string;
  role_code: PlatformRoleCode;
}

export interface PlatformUserEditInput {
  display_name: string;
  email: string | null;
  active: boolean;
  role_code: PlatformRoleCode;
}

export interface EnvironmentHealth {
  status: "ok" | "unavailable";
  application?: string;
  active_application?: string;
  restart_required?: boolean;
  deployment_mode?: string;
  application_type?: string | null;
  storage?: string | null;
  hybrid?: boolean | null;
  details?: string;
}

export interface SessionResponse {
  status: string;
  message: string;
  csrf_token: string | null;
  user: CurrentUser | null;
}
