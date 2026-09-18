import type {
  BootstrapResponse,
  InitialAdministratorInput,
  AccessControlResponse,
  IdentitySyncApplyResponse,
  IdentitySyncPreviewResponse,
  IdentityMappingCatalogResponse,
  IdentityProvisioningApplyResponse,
  IdentityProvisioningPreviewResponse,
  PlatformRoleCode,
  EnvironmentConfigurationResponse,
  EnvironmentHealth,
  HomeResponse,
  PlanningCycleAdministrationResponse,
  PlanningCycleCreateInput,
  PlanningCycle,
  PlanningApprovalsResponse,
  NotificationsResponse,
  PlanningWorkResponse,
  SessionResponse,
  TaskStatus,
  PlatformUserCreateInput,
  PlatformUserEditInput,
  JobsActivityResponse,
  JobActivityDetail,
  OperationsResponse,
  OperationArtifactCatalog,
  BusinessRuleCatalogResponse,
  OracleFileCatalogResponse,
  OracleFilePurpose,
  BusinessRuleRunInput,
  BusinessRuleRTPDefinitionResponse,
  BusinessRuleRTPImportResponse,
  BusinessRuleRTPRegistryStatusResponse,
  DataMapRunInput,
  DataIntegrationCatalogResponse,
  DataIntegrationRegistrationResponse,
  DataIntegrationRunInput,
  DataImportRunInput,
  MetadataImportCatalogResponse,
  MetadataImportRunInput,
  CubeRefreshRunInput,
  SubstitutionVariableCatalogResponse,
  SubstitutionVariableRunInput,
  UserVariableCatalogResponse,
  UserVariableRunInput,
  ReportCatalogResponse,
  ReportPreflightResponse,
  ReportRegistrationInput,
  ReportRegistrationResponse,
  ReportRunInput,
  PipelineCatalogResponse,
  PipelinePreflightResponse,
  PipelineRegistrationResponse,
  OracleArtifactSyncResponse,
  OracleCatalogResponse,
  PipelineRunInput,
  UploadReceiptResponse,
  OperationAcceptedResponse,
  OperationExecution,
  StandaloneFlowRecoveryAccepted,
  StandaloneFlowRecoveryResponse,
  StandaloneFlowStopResponse,
  DataReviewCubesResponse,
  DataReviewDimensionsResponse,
  DataReviewMembersResponse,
  DataReviewGridResponse,
  DataReviewSliceInput,
  DataReviewValidationInput,
  DataReviewValidationResponse,
  DataReviewComparisonInput,
  DataReviewComparisonResponse,
  DataExplorerViewsResponse,
  DataExplorerViewResponse,
  DataReviewTaskContextResponse,
  PlanningValidationEvidence,
  AgentStatusResponse,
  AgentConversation,
  AgentConversationsResponse,
  AgentMessagesResponse,
  AgentSendResponse,
  AgentClarificationRefreshResponse,
  AgentActionDraft,
  AutomationScheduleInput,
  AutomationSchedulesResponse,
  AutomationScheduleRunsResponse,
  ScheduleMutationResponse,
  SchedulePreviewResponse
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly requestId: string | null;

  constructor(message: string, status: number, requestId: string | null = null) {
    super(requestId ? `${message} Reference: ${requestId}.` : message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
  }
}

function versionedApiPath(path: string): string {
  if (!path.startsWith("/api/") || path.startsWith("/api/v1/")) return path;
  return `/api/v1/${path.slice("/api/".length)}`;
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  csrfToken?: string
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(versionedApiPath(path), {
    ...options,
    headers,
    credentials: "include"
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as
      | { detail?: string; details?: string; message?: string }
      | null;
    throw new ApiError(
      payload?.detail ?? payload?.details ?? payload?.message ?? "The request could not be completed.",
      response.status,
      response.headers.get("X-Request-ID")
    );
  }
  return (await response.json()) as T;
}

async function requestBlob(
  path: string,
  payload: object,
  csrfToken: string,
  accept = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
): Promise<Blob> {
  const headers = new Headers({
    "Accept": accept,
    "Content-Type": "application/json",
    "X-CSRF-Token": csrfToken
  });
  const response = await fetch(versionedApiPath(path), {
    method: "POST",
    headers,
    credentials: "include",
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    const details = (await response.json().catch(() => null)) as
      | { detail?: string; details?: string; message?: string }
      | null;
    throw new ApiError(
      details?.detail ?? details?.details ?? details?.message ?? "The export could not be created.",
      response.status,
      response.headers.get("X-Request-ID")
    );
  }
  return response.blob();
}

export const api = {
  bootstrap: () => request<BootstrapResponse>("/api/v1/bootstrap"),
  bootstrapAdministrator: (
    payload: InitialAdministratorInput,
    csrfToken: string
  ) => request<SessionResponse>(
    "/api/v1/access-control/bootstrap",
    { method: "POST", body: JSON.stringify(payload) },
    csrfToken
  ),
  home: () => request<HomeResponse>("/api/v1/home"),
  planningWork: (cycleId?: number) => request<PlanningWorkResponse>(
    cycleId ? `/api/v1/planning-tasks?cycle_id=${cycleId}` : "/api/v1/planning-tasks"
  ),
  cycleAdministration: () => request<PlanningCycleAdministrationResponse>(
    "/api/v1/planning-cycle-administration"
  ),
  createPlanningCycle: (payload: PlanningCycleCreateInput, csrfToken: string) =>
    request<{ status: string; cycle: PlanningCycle }>(
      "/api/v1/planning-cycles",
      {
        method: "POST",
        body: JSON.stringify(payload)
      },
      csrfToken
    ),
  approvals: () => request<PlanningApprovalsResponse>("/api/v1/planning-approvals"),
  submitForApproval: (taskId: number, csrfToken: string) =>
    request<PlanningApprovalsResponse>(
      `/api/v1/planning-tasks/${taskId}/submit-for-approval`,
      { method: "POST" },
      csrfToken
    ),
  decideApproval: (approvalId: number, decision: "APPROVED" | "RETURNED", comment: string, csrfToken: string) =>
    request<{ status: string }>(
      `/api/v1/planning-approvals/${approvalId}`,
      { method: "PATCH", body: JSON.stringify({ decision, comment }) },
      csrfToken
    ),
  notifications: () => request<NotificationsResponse>("/api/v1/notifications"),
  accessControl: () => request<AccessControlResponse>("/api/v1/access-control"),
  previewIdentitySync: (csrfToken: string) => request<IdentitySyncPreviewResponse>(
    "/api/v1/access-control/identity-sync/preview",
    { method: "POST" },
    csrfToken
  ),
  applyIdentitySync: (snapshotChecksum: string, csrfToken: string) => request<IdentitySyncApplyResponse>(
    "/api/v1/access-control/identity-sync/apply",
    { method: "POST", body: JSON.stringify({ snapshot_checksum: snapshotChecksum }) },
    csrfToken
  ),
  identityMappingCatalog: () => request<IdentityMappingCatalogResponse>(
    "/api/v1/access-control/identity-mappings"
  ),
  setIdentityMapping: (entitlementId: number, roleCode: PlatformRoleCode, csrfToken: string) => request<{ status: string }>(
    `/api/v1/access-control/identity-mappings/${entitlementId}`,
    { method: "PUT", body: JSON.stringify({ role_code: roleCode }) },
    csrfToken
  ),
  removeIdentityMapping: (entitlementId: number, csrfToken: string) => request<{ status: string }>(
    `/api/v1/access-control/identity-mappings/${entitlementId}`,
    { method: "DELETE" },
    csrfToken
  ),
  previewIdentityProvisioning: (csrfToken: string) => request<IdentityProvisioningPreviewResponse>(
    "/api/v1/access-control/identity-provisioning/preview",
    { method: "POST" },
    csrfToken
  ),
  applyIdentityProvisioning: (provisioningChecksum: string, csrfToken: string) => request<IdentityProvisioningApplyResponse>(
    "/api/v1/access-control/identity-provisioning/apply",
    { method: "POST", body: JSON.stringify({ provisioning_checksum: provisioningChecksum }) },
    csrfToken
  ),
  jobsActivity: () => request<JobsActivityResponse>("/api/v1/jobs"),
  schedules: () => request<AutomationSchedulesResponse>("/api/schedules"),
  scheduleRuns: (query = "") => request<AutomationScheduleRunsResponse>(
    `/api/schedules/runs/history${query ? `?${query}` : ""}`
  ),
  previewSchedule: (payload: AutomationScheduleInput, csrfToken: string) =>
    request<SchedulePreviewResponse>(
      "/api/schedules/preview",
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  createSchedule: (payload: AutomationScheduleInput, csrfToken: string) =>
    request<ScheduleMutationResponse>(
      "/api/schedules",
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  updateSchedule: (scheduleId: number, payload: AutomationScheduleInput, csrfToken: string) =>
    request<ScheduleMutationResponse>(
      `/api/schedules/${scheduleId}`,
      { method: "PUT", body: JSON.stringify(payload) },
      csrfToken
    ),
  setScheduleEnabled: (scheduleId: number, enabled: boolean, csrfToken: string) =>
    request<ScheduleMutationResponse>(
      `/api/schedules/${scheduleId}/enabled`,
      { method: "PATCH", body: JSON.stringify({ enabled }) },
      csrfToken
    ),
  deleteSchedule: (scheduleId: number, csrfToken: string) =>
    request<{ status: string; message: string }>(
      `/api/schedules/${scheduleId}`,
      { method: "DELETE" },
      csrfToken
    ),
  operations: () => request<OperationsResponse>("/api/v1/operations"),
  agentStatus: () => request<AgentStatusResponse>("/api/agent/status"),
  agentConversations: () => request<AgentConversationsResponse>("/api/agent/conversations"),
  createAgentConversation: (csrfToken: string) =>
    request<{ status: string; conversation: AgentConversation }>(
      "/api/agent/conversations",
      { method: "POST" },
      csrfToken
    ),
  agentMessages: (conversationId: string) =>
    request<AgentMessagesResponse>(
      `/api/agent/conversations/${encodeURIComponent(conversationId)}/messages`
    ),
  sendAgentMessage: (conversationId: string, content: string, csrfToken: string) =>
    request<AgentSendResponse>(
      `/api/agent/conversations/${encodeURIComponent(conversationId)}/messages`,
      { method: "POST", body: JSON.stringify({ content }) },
      csrfToken
    ),
  resolveAgentApproval: (
    conversationId: string,
    requestId: string,
    decision: "approve" | "reject",
    csrfToken: string
  ) =>
    request<AgentSendResponse>(
      `/api/agent/conversations/${encodeURIComponent(conversationId)}/approval`,
      {
        method: "POST",
        body: JSON.stringify({ request_id: requestId, decision })
      },
      csrfToken
    ),
  resolveAgentClarification: (
    conversationId: string,
    requestId: string,
    value: string | null,
    csrfToken: string
  ) =>
    request<AgentSendResponse>(
      `/api/agent/conversations/${encodeURIComponent(conversationId)}/clarification`,
      {
        method: "POST",
        body: JSON.stringify({ request_id: requestId, value })
      },
      csrfToken
    ),
  synchronizeAgentArtifacts: (
    conversationId: string,
    requestId: string,
    csrfToken: string
  ) =>
    request<AgentClarificationRefreshResponse>(
      `/api/agent/conversations/${encodeURIComponent(conversationId)}/artifacts/synchronize`,
      { method: "POST", body: JSON.stringify({ request_id: requestId }) },
      csrfToken
    ),
  registerAgentArtifact: (
    conversationId: string,
    requestId: string,
    identifier: string,
    csrfToken: string
  ) =>
    request<AgentSendResponse>(
      `/api/agent/conversations/${encodeURIComponent(conversationId)}/artifacts/register`,
      { method: "POST", body: JSON.stringify({ request_id: requestId, identifier }) },
      csrfToken
    ),
  resolveAgentInput: (
    conversationId: string,
    requestId: string,
    values: Record<string, unknown> | null,
    csrfToken: string
  ) =>
    request<AgentSendResponse>(
      `/api/agent/conversations/${encodeURIComponent(conversationId)}/inputs`,
      {
        method: "POST",
        body: JSON.stringify({ request_id: requestId, values })
      },
      csrfToken
    ),
  deleteAgentConversation: (conversationId: string, csrfToken: string) =>
    request<{ status: string }>(
      `/api/agent/conversations/${encodeURIComponent(conversationId)}`,
      { method: "DELETE" },
      csrfToken
    ),
  updateAgentDraftInputs: (draftId: string, inputs: Record<string, unknown>, csrfToken: string) =>
    request<{ status: string; action_draft: AgentActionDraft }>(
      `/api/agent/action-drafts/${encodeURIComponent(draftId)}/inputs`,
      { method: "PATCH", body: JSON.stringify({ inputs }) },
      csrfToken
    ),
  preflightAgentDraft: (draftId: string, csrfToken: string) =>
    request<{ status: string; action_draft: AgentActionDraft }>(
      `/api/agent/action-drafts/${encodeURIComponent(draftId)}/preflight`,
      { method: "POST" },
      csrfToken
    ),
  agentOperationHandoff: (draftId: string, targetCode: string) =>
    request<{ status: string; action_draft: AgentActionDraft }>(
      `/api/agent/action-drafts/${encodeURIComponent(draftId)}/handoff?target_code=${encodeURIComponent(targetCode)}`
    ),
  dataReviewCubes: () => request<DataReviewCubesResponse>("/api/data-review/cubes"),
  dataReviewTaskContext: (taskId: number) =>
    request<DataReviewTaskContextResponse>(
      `/api/v1/planning-tasks/${taskId}/data-review-context`
    ),
  acknowledgePlanningValidation: (validationId: number, csrfToken: string) =>
    request<{ status: string; validation: PlanningValidationEvidence }>(
      `/api/v1/planning-validations/${validationId}/acknowledge`,
      { method: "POST" },
      csrfToken
    ),
  dataReviewDimensions: (cube: string) =>
    request<DataReviewDimensionsResponse>(
      `/api/data-review/cubes/${encodeURIComponent(cube)}/dimensions`
    ),
  dataReviewMembers: (cube: string, dimension: string, query = "", offset = 0, limit = 40) =>
    request<DataReviewMembersResponse>(
      `/api/data-review/cubes/${encodeURIComponent(cube)}/dimensions/${encodeURIComponent(dimension)}/members?q=${encodeURIComponent(query)}&offset=${offset}&limit=${limit}`
    ),
  dataReviewGrid: (payload: DataReviewSliceInput, csrfToken: string) =>
    request<DataReviewGridResponse>(
      "/api/data-review/grid",
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  exportDataReviewGrid: (payload: DataReviewSliceInput, csrfToken: string) =>
    requestBlob("/api/data-review/grid/export", payload, csrfToken),
  exportDataReviewGridCsv: (payload: DataReviewSliceInput, csrfToken: string) =>
    requestBlob("/api/data-review/grid/export/csv", payload, csrfToken, "text/csv"),
  dataExplorerViews: () =>
    request<DataExplorerViewsResponse>("/api/data-explorer/views"),
  saveDataExplorerView: (payload: ReportRegistrationInput, csrfToken: string) =>
    request<DataExplorerViewResponse>(
      "/api/data-explorer/views",
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  deleteDataExplorerView: (name: string, csrfToken: string) =>
    request<{ status: string; message: string }>(
      `/api/data-explorer/views/${encodeURIComponent(name)}`,
      { method: "DELETE" },
      csrfToken
    ),
  validateDataReview: (payload: DataReviewValidationInput, csrfToken: string) =>
    request<DataReviewValidationResponse>(
      "/api/data-review/validate",
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  compareDataReview: (payload: DataReviewComparisonInput, csrfToken: string) =>
    request<DataReviewComparisonResponse>(
      "/api/data-review/compare",
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  exportDataReviewValidation: (payload: DataReviewValidationInput, csrfToken: string) =>
    requestBlob("/api/data-review/validate/export", payload, csrfToken),
  exportDataReviewComparison: (payload: DataReviewComparisonInput, csrfToken: string) =>
    requestBlob("/api/data-review/compare/export", payload, csrfToken),
  operationArtifacts: (code: "business-rules" | "data-maps") =>
    request<OperationArtifactCatalog>(`/api/operations/${code}/catalog`),
  dataIntegrationCatalog: () =>
    request<DataIntegrationCatalogResponse>("/api/operations/data-integrations/catalog"),
  dataImportCatalog: () =>
    request<OperationArtifactCatalog>("/api/operations/data-import/catalog"),
  oracleFileCatalog: (purpose: OracleFilePurpose) =>
    request<OracleFileCatalogResponse>(
      `/api/operations/files/catalog?purpose=${encodeURIComponent(purpose)}`
    ),
  metadataImportCatalog: () =>
    request<MetadataImportCatalogResponse>("/api/operations/metadata-import/catalog"),
  cubeRefreshCatalog: () =>
    request<OperationArtifactCatalog>("/api/operations/cube-refresh/catalog"),
  substitutionVariableCatalog: () =>
    request<SubstitutionVariableCatalogResponse>("/api/substitution-variables/catalog"),
  userVariableCatalog: (userName?: string) =>
    request<UserVariableCatalogResponse>(
      `/api/user-variables/catalog${userName ? `?user_name=${encodeURIComponent(userName)}` : ""}`
    ),
  reportCatalog: () => request<ReportCatalogResponse>("/api/reports/catalog"),
  reportPreflight: (formName: string, csrfToken: string) =>
    request<ReportPreflightResponse>(
      "/api/reports/preflight",
      { method: "POST", body: JSON.stringify({ form_name: formName }) },
      csrfToken
    ),
  registerReport: (payload: ReportRegistrationInput, csrfToken: string) =>
    request<ReportRegistrationResponse>(
      "/api/reports/catalog",
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  pipelineCatalog: () =>
    request<PipelineCatalogResponse>("/api/operations/pipelines/catalog"),
  pipelinePreflight: (pipelineCode: string) =>
    request<PipelinePreflightResponse>(
      `/api/operations/pipelines/${encodeURIComponent(pipelineCode)}/preflight`
    ),
  registerPipeline: (pipelineCode: string, csrfToken: string) =>
    request<PipelineRegistrationResponse>(
      "/api/operations/pipelines/register",
      { method: "POST", body: JSON.stringify({ pipeline_code: pipelineCode }) },
      csrfToken
    ),
  registerDataIntegration: (integrationName: string, csrfToken: string) =>
    request<DataIntegrationRegistrationResponse>(
      "/api/operations/data-integrations/register",
      { method: "POST", body: JSON.stringify({ integration_name: integrationName }) },
      csrfToken
    ),
  synchronizeOracleCatalog: (csrfToken: string) =>
    request<OracleArtifactSyncResponse>(
      "/api/operations/oracle-catalog/sync",
      { method: "POST" },
      csrfToken
    ),
  oracleCatalog: () =>
    request<OracleCatalogResponse>("/api/operations/oracle-catalog"),
  uploadOperationFile: (file: File, csrfToken: string) =>
    request<UploadReceiptResponse>(
      `/api/uploads?filename=${encodeURIComponent(file.name)}`,
      { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file },
      csrfToken
    ),
  startBusinessRule: (payload: BusinessRuleRunInput, csrfToken: string) =>
    request<OperationAcceptedResponse>(
      "/api/operations/business-rules/runs",
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  businessRuleRTPDefinition: (ruleName: string) =>
    request<BusinessRuleRTPDefinitionResponse>(
      `/api/operations/business-rules/rtp-definition?rule_name=${encodeURIComponent(ruleName)}`
    ),
  businessRuleCatalog: () =>
    request<BusinessRuleCatalogResponse>(
      "/api/operations/business-rules/catalog"
    ),
  businessRuleRTPRegistryStatus: () =>
    request<BusinessRuleRTPRegistryStatusResponse>(
      "/api/operations/business-rules/rtp-registry/status"
    ),
  importBusinessRuleRTPRegistry: (file: File, csrfToken: string) =>
    request<BusinessRuleRTPImportResponse>(
      `/api/operations/business-rules/rtp-registry/import?filename=${encodeURIComponent(file.name)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: file
      },
      csrfToken
    ),
  startDataMap: (payload: DataMapRunInput, csrfToken: string) =>
    request<OperationAcceptedResponse>(
      "/api/operations/data-maps/runs",
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  startDataIntegration: (payload: DataIntegrationRunInput, csrfToken: string) =>
    request<OperationAcceptedResponse>(
      "/api/operations/data-integrations/runs",
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  startDataImport: (payload: DataImportRunInput, csrfToken: string) =>
    request<OperationAcceptedResponse>(
      "/api/operations/data-import/runs",
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  startMetadataImport: (payload: MetadataImportRunInput, csrfToken: string) =>
    request<OperationAcceptedResponse>(
      "/api/operations/metadata-import/runs",
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  startCubeRefresh: (payload: CubeRefreshRunInput, csrfToken: string) =>
    request<OperationAcceptedResponse>(
      "/api/operations/cube-refresh/runs",
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  startSubstitutionVariable: (payload: SubstitutionVariableRunInput, csrfToken: string) =>
    request<OperationAcceptedResponse>(
      "/api/operations/substitution-variables/runs",
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  startUserVariable: (payload: UserVariableRunInput, csrfToken: string) =>
    request<OperationAcceptedResponse>(
      "/api/operations/user-variables/runs",
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  startReport: (payload: ReportRunInput, csrfToken: string) =>
    request<OperationAcceptedResponse>(
      "/api/operations/reports/runs",
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  startPipeline: (payload: PipelineRunInput, csrfToken: string) =>
    request<OperationAcceptedResponse>(
      "/api/operations/pipelines/runs",
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  operationRun: (executionId: string) =>
      request<OperationExecution>(`/api/operations/runs/${encodeURIComponent(executionId)}`),
    stopStandaloneFlow: (executionId: string, csrfToken: string) =>
      request<StandaloneFlowStopResponse>(
        `/api/operations/runs/${encodeURIComponent(executionId)}/stop`,
        { method: "POST" },
        csrfToken
      ),
    standaloneFlowRecovery: (executionId: string) =>
      request<StandaloneFlowRecoveryResponse>(`/api/operations/runs/${encodeURIComponent(executionId)}/recovery`),
    retryStandaloneFlow: (
      executionId: string,
      payload: {
        failed_step_sequence: number;
        confirmation: "RETRY_FROM_FAILED_STEP";
        replacement_uploads: Record<string, string>;
      },
      csrfToken: string
    ) => request<StandaloneFlowRecoveryAccepted>(
      `/api/operations/runs/${encodeURIComponent(executionId)}/recovery`,
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  jobActivity: (executionId: string) =>
    request<{ status: string; job: JobActivityDetail }>(
      `/api/v1/jobs/${encodeURIComponent(executionId)}`
    ),
  createPlatformUser: (payload: PlatformUserCreateInput, csrfToken: string) =>
    request<{ status: string }>(
      "/api/v1/access-control/users",
      { method: "POST", body: JSON.stringify(payload) },
      csrfToken
    ),
  updatePlatformUser: (userId: number, payload: PlatformUserEditInput, csrfToken: string) =>
    request<{ status: string }>(
      `/api/v1/access-control/users/${userId}`,
      { method: "PATCH", body: JSON.stringify(payload) },
      csrfToken
    ),
  resetPlatformPassword: (userId: number, password: string, csrfToken: string) =>
    request<{ status: string; message: string }>(
      `/api/v1/access-control/users/${userId}/password`,
      { method: "POST", body: JSON.stringify({ password, password_confirmation: password }) },
      csrfToken
    ),
  readNotification: (notificationId: number, csrfToken: string) =>
    request<{ status: string }>(
      `/api/v1/notifications/${notificationId}/read`,
      { method: "PATCH" },
      csrfToken
    ),
  readAllNotifications: (csrfToken: string) =>
    request<{ status: string }>(
      "/api/v1/notifications/read-all",
      { method: "POST" },
      csrfToken
    ),
  health: () => request<EnvironmentHealth>("/api/health"),
  environmentConfiguration: () =>
    request<EnvironmentConfigurationResponse>(
      "/api/v1/environment/configuration"
    ),
  discoverEnvironmentApplications: (csrfToken: string) =>
    request<EnvironmentConfigurationResponse>(
      "/api/v1/environment/applications/discover",
      { method: "POST" },
      csrfToken
    ),
  selectEnvironmentApplication: (
    applicationName: string,
    csrfToken: string
  ) => request<EnvironmentConfigurationResponse>(
    "/api/v1/environment/application",
    {
      method: "PUT",
      body: JSON.stringify({ application_name: applicationName })
    },
    csrfToken
  ),
  login: (username: string, password: string, csrfToken: string) =>
    request<SessionResponse>(
      "/api/v1/session",
      {
        method: "POST",
        body: JSON.stringify({ username, password })
      },
      csrfToken
    ),
  oracleLogin: (username: string, password: string, csrfToken: string) =>
    request<SessionResponse>(
      "/api/v1/session/oracle",
      {
        method: "POST",
        body: JSON.stringify({ username, password })
      },
      csrfToken
    ),
  logout: (csrfToken: string) =>
    request<SessionResponse>(
      "/api/v1/session",
      { method: "DELETE" },
      csrfToken
    ),
  updateTask: (taskId: number, status: TaskStatus, csrfToken: string) =>
    request<{ status: string }>(
      `/api/v1/planning-tasks/${taskId}/status`,
      {
        method: "PATCH",
        body: JSON.stringify({ status })
      },
      csrfToken
    )
};
