import { lazy, Suspense, useCallback, useEffect, useState } from "react";

import { api, ApiError } from "./api/client";
import type { AccessControlResponse, BootstrapResponse, EnvironmentHealth, HomeResponse, IdentityMappingCatalogResponse, IdentityProvisioningPreview, IdentitySyncPreview, InitialAdministratorInput, JobActivityDetail, JobsActivityResponse, NotificationsResponse, OperationsResponse, PlanningApprovalsResponse, PlanningCycleAdministrationResponse, PlanningCycleCreateInput, PlanningWorkResponse, PlatformRoleCode, PlatformUserCreateInput, PlatformUserEditInput, TaskStatus } from "./api/types";
import { AppShell } from "./components/AppShell";
import { Dashboard } from "./components/Dashboard";
import { FullPageLoading, ToastMessage, UnavailableState, WorkspaceLoading } from "./components/Feedback";
import { LoginPage } from "./components/LoginPage";
import { PlanningWorkspace } from "./components/PlanningWorkspace";
import { PlanningCycleAdministration } from "./components/PlanningCycleAdministration";
import { ApprovalsWorkspace } from "./components/ApprovalsWorkspace";
import { NotificationsWorkspace } from "./components/NotificationsWorkspace";
import { AccessControlWorkspace } from "./components/AccessControlWorkspace";
import { JobsActivityWorkspace } from "./components/JobsActivityWorkspace";
import { OperationsWorkspace } from "./components/OperationsWorkspace";
import { DataReviewWorkspace } from "./components/DataReviewWorkspace";
import { ReportGenerationRunner } from "./components/ReportGenerationRunner";
import { EpmAssistantWorkspace } from "./components/EpmAssistantWorkspace";

const SchedulingWorkspace = lazy(async () => {
  const module = await import("./components/SchedulingWorkspace");
  return { default: module.SchedulingWorkspace };
});

type AppView = "home" | "tasks" | "cycles" | "approvals" | "notifications" | "access" | "jobs" | "operations" | "schedules" | "data-review" | "reports" | "assistant";

export function App() {
  const [bootstrap, setBootstrap] = useState<BootstrapResponse | null>(null);
  const [home, setHome] = useState<HomeResponse | null>(null);
  const [work, setWork] = useState<PlanningWorkResponse | null>(null);
  const [cycleAdministration, setCycleAdministration] = useState<PlanningCycleAdministrationResponse | null>(null);
  const [approvals, setApprovals] = useState<PlanningApprovalsResponse | null>(null);
  const [notifications, setNotifications] = useState<NotificationsResponse | null>(null);
  const [accessControl, setAccessControl] = useState<AccessControlResponse | null>(null);
  const [jobsActivity, setJobsActivity] = useState<JobsActivityResponse | null>(null);
  const [operations, setOperations] = useState<OperationsResponse | null>(null);
  const [jobDetail, setJobDetail] = useState<JobActivityDetail | null>(null);
  const [activeView, setActiveView] = useState<AppView>(() => viewFromHash());
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [busyTaskId, setBusyTaskId] = useState<number | null>(null);
  const [busyApprovalId, setBusyApprovalId] = useState<number | null>(null);
  const [busyUserId, setBusyUserId] = useState<number | "create" | null>(null);
  const [identitySyncBusy, setIdentitySyncBusy] = useState(false);
  const [identitySyncPreview, setIdentitySyncPreview] = useState<IdentitySyncPreview | null>(null);
  const [identityMappings, setIdentityMappings] = useState<IdentityMappingCatalogResponse | null>(null);
  const [identityProvisioningPreview, setIdentityProvisioningPreview] = useState<IdentityProvisioningPreview | null>(null);
  const [busyExecutionId, setBusyExecutionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [health, setHealth] = useState<EnvironmentHealth | null>(null);
  const [healthBusy, setHealthBusy] = useState(false);
  const [scheduleRefreshKey, setScheduleRefreshKey] = useState(0);

  const load = useCallback(async () => {
    setError(null);
    const nextBootstrap = await api.bootstrap();
    setBootstrap(nextBootstrap);
    if (nextBootstrap.authenticated) {
      const [nextHome, nextNotifications] = await Promise.all([api.home(), api.notifications()]);
      setHome(nextHome);
      setNotifications(nextNotifications);
    } else {
      setHome(null);
      setWork(null);
      setCycleAdministration(null);
      setApprovals(null);
      setNotifications(null);
      setAccessControl(null);
      setIdentityMappings(null);
      setIdentitySyncPreview(null);
      setIdentityProvisioningPreview(null);
      setJobsActivity(null);
      setOperations(null);
      setJobDetail(null);
    }
  }, []);

  useEffect(() => {
    load().then(() => {
      const parameters = new URLSearchParams(window.location.search);
      const code = parameters.get("auth_error");
      if (!code) return;
      const messages: Record<string, string> = {
        account_not_ready: "Oracle verified your identity, but your platform account is not ready. Ask a Platform Administrator to synchronize and provision your access.",
        identity_validation_failed: "Oracle sign-in could not be validated. Start the sign-in again.",
        identity_provider_unavailable: "Oracle Cloud Identity is temporarily unavailable. Try again or use administrator recovery access.",
        federated_not_configured: "Oracle sign-in is not configured for this environment."
      };
      setError(messages[code] ?? "Oracle sign-in could not be completed.");
      parameters.delete("auth_error");
      const query = parameters.toString();
      window.history.replaceState({}, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
    }).catch((reason: unknown) => setError(message(reason))).finally(() => setLoading(false));
  }, [load]);

  useEffect(() => {
    const updateView = () => setActiveView(viewFromHash());
    window.addEventListener("hashchange", updateView);
    return () => window.removeEventListener("hashchange", updateView);
  }, []);

  useEffect(() => {
    if (activeView !== "tasks" || !bootstrap?.authenticated || work) return;
    api.planningWork().then(setWork).catch((reason: unknown) => setError(message(reason)));
  }, [activeView, bootstrap, work]);

  useEffect(() => {
    if (activeView !== "cycles" || !bootstrap?.authenticated || cycleAdministration) return;
    api.cycleAdministration().then(setCycleAdministration).catch((reason: unknown) => setError(message(reason)));
  }, [activeView, bootstrap, cycleAdministration]);

  useEffect(() => {
    if (activeView !== "approvals" || !bootstrap?.authenticated || approvals) return;
    api.approvals().then(setApprovals).catch((reason: unknown) => setError(message(reason)));
  }, [activeView, bootstrap, approvals]);

  useEffect(() => {
    if (activeView !== "access" || !bootstrap?.authenticated || accessControl) return;
    api.accessControl().then((response) => {
      setAccessControl(response);
      if (response.identity_sync.provider_registered) {
        void api.identityMappingCatalog().then(setIdentityMappings).catch((reason: unknown) => setError(message(reason)));
      }
    }).catch((reason: unknown) => setError(message(reason)));
  }, [activeView, bootstrap, accessControl]);

  useEffect(() => {
    if (activeView !== "jobs" || !bootstrap?.authenticated || jobsActivity) return;
    api.jobsActivity().then(setJobsActivity).catch((reason: unknown) => setError(message(reason)));
  }, [activeView, bootstrap, jobsActivity]);

  useEffect(() => {
    if (activeView !== "jobs" || !jobsActivity) return;
    const parameters = new URLSearchParams(window.location.search);
    const executionId = parameters.get("execution_id")?.trim();
    if (!executionId) return;
    parameters.delete("execution_id");
    const query = parameters.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${query ? `?${query}` : ""}#jobs`);
    void inspectJob(executionId);
  }, [activeView, jobsActivity]);

  useEffect(() => {
    if (activeView !== "operations" || !bootstrap?.authenticated || operations) return;
    api.operations().then(setOperations).catch((reason: unknown) => setError(message(reason)));
  }, [activeView, bootstrap, operations]);

  useEffect(() => {
    if (activeView !== "jobs" || !jobsActivity?.summary.running) return;
    const timer = window.setInterval(() => {
      api.jobsActivity().then(setJobsActivity).catch(() => undefined);
      if (jobDetail?.status === "RUNNING") {
        api.jobActivity(jobDetail.execution_id)
          .then((response) => setJobDetail(response.job))
          .catch(() => undefined);
      }
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [activeView, jobDetail, jobsActivity?.summary.running]);

  useEffect(() => {
    const tasks = activeView === "tasks" ? work?.tasks : home?.tasks;
    const hasActiveTaskExecution = tasks?.some((task) =>
      ["QUEUED", "RUNNING"].includes(task.execution_attempts?.[0]?.status ?? "")
    );
    if (!bootstrap?.authenticated || !hasActiveTaskExecution) return;
    const timer = window.setInterval(() => {
      api.home().then(setHome).catch(() => undefined);
      if (activeView === "tasks") {
        api.planningWork().then(setWork).catch(() => undefined);
      }
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [activeView, bootstrap?.authenticated, home?.tasks, work?.tasks]);

  async function login(username: string, password: string) {
    if (!bootstrap) return;
    setBusy(true);
    setError(null);
    try {
      await api.login(username, password, bootstrap.csrf_token);
      await load();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function oracleLogin(username: string, password: string) {
    if (!bootstrap) return;
    setBusy(true);
    setError(null);
    try {
      await api.oracleLogin(username, password, bootstrap.csrf_token);
      await load();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function bootstrapAdministrator(input: InitialAdministratorInput) {
    if (!bootstrap) return;
    setBusy(true);
    setError(null);
    try {
      await api.bootstrapAdministrator(input, bootstrap.csrf_token);
      await load();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    if (!bootstrap) return;
    setBusy(true);
    try {
      await api.logout(bootstrap.csrf_token);
      await load();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function refresh() {
    setBusy(true);
    try {
      await load();
      if (activeView === "tasks") setWork(await api.planningWork());
      else if (activeView === "cycles") setCycleAdministration(await api.cycleAdministration());
      else if (activeView === "approvals") setApprovals(await api.approvals());
      else if (activeView === "access") setAccessControl(await api.accessControl());
      else if (activeView === "jobs") setJobsActivity(await api.jobsActivity());
      else if (activeView === "operations") setOperations(await api.operations());
      else if (activeView === "schedules") setScheduleRefreshKey((current) => current + 1);
      else {
        setWork(null);
        setCycleAdministration(null);
      }
      setNotice(activeView === "tasks" ? "My Work refreshed with the latest assignments." : activeView === "cycles" ? "Planning cycles refreshed." : activeView === "schedules" ? "Schedules refreshed." : "Homepage refreshed with the latest platform state.");
      window.setTimeout(() => setNotice(null), 3500);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function submitForApproval(taskId: number) {
    if (!bootstrap) return;
    setBusyTaskId(taskId);
    setError(null);
    try {
      await api.submitForApproval(taskId, bootstrap.csrf_token);
      const [nextHome, nextWork, nextApprovals, nextNotifications] = await Promise.all([
        api.home(),
        activeView === "tasks" ? api.planningWork() : Promise.resolve(null),
        api.approvals(),
        api.notifications()
      ]);
      setHome(nextHome);
      setWork(nextWork);
      setApprovals(nextApprovals);
      setNotifications(nextNotifications);
      setNotice("Planning work submitted. The assigned reviewer has been notified.");
      window.setTimeout(() => setNotice(null), 4000);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusyTaskId(null);
    }
  }

  async function decideApproval(approvalId: number, decision: "APPROVED" | "RETURNED", comment: string) {
    if (!bootstrap) return;
    setBusyApprovalId(approvalId);
    setError(null);
    try {
      await api.decideApproval(approvalId, decision, comment, bootstrap.csrf_token);
      const [nextHome, nextApprovals, nextNotifications] = await Promise.all([
        api.home(), api.approvals(), api.notifications()
      ]);
      setHome(nextHome);
      setApprovals(nextApprovals);
      setNotifications(nextNotifications);
      setWork(null);
      setNotice(decision === "APPROVED" ? "Planning submission approved." : "Submission returned to the planner with your guidance.");
      window.setTimeout(() => setNotice(null), 4000);
    } catch (reason) {
      setError(message(reason));
      throw reason;
    } finally {
      setBusyApprovalId(null);
    }
  }

  async function readNotification(notificationId: number) {
    if (!bootstrap) return;
    await api.readNotification(notificationId, bootstrap.csrf_token);
    setNotifications(await api.notifications());
  }

  async function readAllNotifications() {
    if (!bootstrap) return;
    setBusy(true);
    try {
      await api.readAllNotifications(bootstrap.csrf_token);
      setNotifications(await api.notifications());
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function createCycle(payload: PlanningCycleCreateInput) {
    if (!bootstrap) return;
    setBusy(true);
    setError(null);
    try {
      await api.createPlanningCycle(payload, bootstrap.csrf_token);
      const [nextHome, nextAdministration] = await Promise.all([
        api.home(),
        api.cycleAdministration()
      ]);
      setHome(nextHome);
      setCycleAdministration(nextAdministration);
      setWork(null);
      setNotice("Planning cycle opened. Assigned responsibilities are now available in My Work.");
      window.setTimeout(() => setNotice(null), 4500);
    } catch (reason) {
      setError(message(reason));
      throw reason;
    } finally {
      setBusy(false);
    }
  }

  async function createPlatformUser(payload: PlatformUserCreateInput) {
    if (!bootstrap) return;
    setBusyUserId("create");
    setError(null);
    try {
      await api.createPlatformUser(payload, bootstrap.csrf_token);
      setAccessControl(await api.accessControl());
      setNotice(`${payload.display_name} can now sign in to the platform.`);
      window.setTimeout(() => setNotice(null), 4000);
    } catch (reason) {
      setError(message(reason));
      throw reason;
    } finally {
      setBusyUserId(null);
    }
  }

  async function updatePlatformUser(userId: number, payload: PlatformUserEditInput) {
    if (!bootstrap) return;
    setBusyUserId(userId);
    setError(null);
    try {
      await api.updatePlatformUser(userId, payload, bootstrap.csrf_token);
      setAccessControl(await api.accessControl());
      setNotice("User access updated successfully.");
      window.setTimeout(() => setNotice(null), 4000);
    } catch (reason) {
      setError(message(reason));
      throw reason;
    } finally {
      setBusyUserId(null);
    }
  }

  async function resetPlatformPassword(userId: number, password: string) {
    if (!bootstrap) return;
    setBusyUserId(userId);
    setError(null);
    try {
      await api.resetPlatformPassword(userId, password, bootstrap.csrf_token);
      setNotice("Platform password updated. The previous password no longer works.");
      window.setTimeout(() => setNotice(null), 4500);
    } catch (reason) {
      setError(message(reason));
      throw reason;
    } finally {
      setBusyUserId(null);
    }
  }

  async function previewOracleIdentitySync() {
    if (!bootstrap) return;
    setIdentitySyncBusy(true);
    setError(null);
    try {
      const response = await api.previewIdentitySync(bootstrap.csrf_token);
      setIdentitySyncPreview(response.preview);
    } catch (reason) {
      setError(message(reason));
      throw reason;
    } finally {
      setIdentitySyncBusy(false);
    }
  }

  async function applyOracleIdentitySync() {
    if (!bootstrap || !identitySyncPreview) return;
    setIdentitySyncBusy(true);
    setError(null);
    try {
      const response = await api.applyIdentitySync(
        identitySyncPreview.snapshot_checksum,
        bootstrap.csrf_token
      );
      setAccessControl(await api.accessControl());
      setIdentityMappings(await api.identityMappingCatalog());
      setIdentitySyncPreview(null);
      setNotice(response.message);
      window.setTimeout(() => setNotice(null), 5000);
    } catch (reason) {
      setError(message(reason));
      throw reason;
    } finally {
      setIdentitySyncBusy(false);
    }
  }

  async function setOracleIdentityMapping(entitlementId: number, roleCode: PlatformRoleCode | null) {
    if (!bootstrap) return;
    setIdentitySyncBusy(true);
    setError(null);
    try {
      if (roleCode) await api.setIdentityMapping(entitlementId, roleCode, bootstrap.csrf_token);
      else await api.removeIdentityMapping(entitlementId, bootstrap.csrf_token);
      setIdentityMappings(await api.identityMappingCatalog());
      setIdentityProvisioningPreview(null);
    } catch (reason) {
      setError(message(reason));
      throw reason;
    } finally {
      setIdentitySyncBusy(false);
    }
  }

  async function previewOracleProvisioning() {
    if (!bootstrap) return;
    setIdentitySyncBusy(true);
    setError(null);
    try {
      const response = await api.previewIdentityProvisioning(bootstrap.csrf_token);
      setIdentityProvisioningPreview(response.preview);
    } catch (reason) {
      setError(message(reason));
      throw reason;
    } finally {
      setIdentitySyncBusy(false);
    }
  }

  async function applyOracleProvisioning() {
    if (!bootstrap || !identityProvisioningPreview) return;
    setIdentitySyncBusy(true);
    setError(null);
    try {
      const response = await api.applyIdentityProvisioning(
        identityProvisioningPreview.checksum,
        bootstrap.csrf_token
      );
      setAccessControl(await api.accessControl());
      setIdentityProvisioningPreview(null);
      setNotice(response.message);
      window.setTimeout(() => setNotice(null), 5000);
    } catch (reason) {
      setError(message(reason));
      throw reason;
    } finally {
      setIdentitySyncBusy(false);
    }
  }

  async function inspectJob(executionId: string) {
    setBusyExecutionId(executionId);
    setError(null);
    try {
      const response = await api.jobActivity(executionId);
      setJobDetail(response.job);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusyExecutionId(null);
    }
  }

  async function openScheduledExecution(executionId: string) {
    setJobsActivity(await api.jobsActivity());
    window.location.hash = "#jobs";
    await inspectJob(executionId);
  }

  async function openRecoveredExecution(executionId: string) {
    setJobsActivity(await api.jobsActivity());
    setNotice("Recovery was approved and queued as a new linked execution.");
    window.setTimeout(() => setNotice(null), 3500);
    await inspectJob(executionId);
  }

  async function updateTask(taskId: number, status: TaskStatus) {
    if (!bootstrap) return;
    setBusyTaskId(taskId);
    setError(null);
    try {
      await api.updateTask(taskId, status, bootstrap.csrf_token);
      const [nextHome, nextWork] = await Promise.all([
        api.home(),
        activeView === "tasks" ? api.planningWork() : Promise.resolve(null)
      ]);
      setHome(nextHome);
      setWork(nextWork);
      setNotice(status === "COMPLETED" ? "Task completed and cycle progress updated." : "Task is now in progress.");
      window.setTimeout(() => setNotice(null), 3500);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusyTaskId(null);
    }
  }

  async function checkHealth() {
    setHealthBusy(true);
    let environmentApplication = bootstrap?.environment?.application_name;
    let deploymentMode = bootstrap?.environment?.deployment_mode;
    try {
      const configuration = await api.environmentConfiguration().catch(() => null);
      environmentApplication = configuration?.selected_application
        ?? configuration?.active_application
        ?? environmentApplication;
      deploymentMode = configuration?.deployment_mode ?? deploymentMode;
      const result = await api.health();
      setHealth({
        ...result,
        application: result.application ?? environmentApplication,
        deployment_mode: result.deployment_mode ?? deploymentMode
      });
    } catch (reason) {
      setHealth({
        status: "unavailable",
        application: environmentApplication,
        active_application: bootstrap?.environment?.application_name,
        restart_required: Boolean(
          environmentApplication
          && bootstrap?.environment?.application_name
          && environmentApplication.toLowerCase()
            !== bootstrap.environment.application_name.toLowerCase()
        ),
        deployment_mode: deploymentMode,
        details: message(reason)
      });
    } finally {
      setHealthBusy(false);
    }
  }

  if (loading) return <FullPageLoading />;
  if (!bootstrap) return <UnavailableState error={error ?? "The platform could not be initialized."} onRetry={refresh} />;
  if (!bootstrap.authenticated) {
    return <LoginPage productName={bootstrap.product.name} company={bootstrap.product.company} busy={busy} error={error} requiresBootstrap={bootstrap.requires_bootstrap} identityAuthentication={bootstrap.identity_authentication} onLogin={login} onOracleLogin={oracleLogin} onBootstrap={bootstrapAdministrator} />;
  }
  if (!home) return <UnavailableState error={error ?? "Your Planning workspace is unavailable."} onRetry={refresh} />;

  return (
    <AppShell bootstrap={bootstrap} activeView={activeView} busy={busy} unreadNotifications={notifications?.unread_count ?? 0} onLogout={logout} onRefresh={refresh}>
      {notice && <ToastMessage tone="success" message={notice} onDismiss={() => setNotice(null)} />}
      {error && <ToastMessage tone="error" message={error} onDismiss={() => setError(null)} />}
      {activeView === "tasks" ? (
        work
          ? <PlanningWorkspace work={work} busyTaskId={busyTaskId} onTaskStatus={updateTask} onSubmitApproval={submitForApproval} />
          : <WorkspaceLoading />
      ) : activeView === "cycles" ? (
        cycleAdministration
          ? <PlanningCycleAdministration data={cycleAdministration} busy={busy} onCreate={createCycle} />
          : <WorkspaceLoading label="Planning cycles" message="Preparing the business calendar and assignment choices…" />
      ) : activeView === "approvals" ? (
        approvals
          ? <ApprovalsWorkspace approvals={approvals.approvals} busyApprovalId={busyApprovalId} onDecision={decideApproval} />
          : <WorkspaceLoading label="Approvals" message="Preparing your review queue…" />
      ) : activeView === "notifications" ? (
        notifications
          ? <NotificationsWorkspace data={notifications} busy={busy} onRead={readNotification} onReadAll={readAllNotifications} />
          : <WorkspaceLoading label="Notifications" message="Preparing your Planning inbox…" />
      ) : activeView === "access" ? (
        accessControl
          ? <AccessControlWorkspace data={accessControl} busyUserId={busyUserId} identitySyncBusy={identitySyncBusy} identitySyncPreview={identitySyncPreview} identityMappings={identityMappings} identityProvisioningPreview={identityProvisioningPreview} onCreate={createPlatformUser} onUpdate={updatePlatformUser} onResetPassword={resetPlatformPassword} onPreviewIdentitySync={previewOracleIdentitySync} onApplyIdentitySync={applyOracleIdentitySync} onCancelIdentitySync={() => setIdentitySyncPreview(null)} onSetIdentityMapping={setOracleIdentityMapping} onPreviewProvisioning={previewOracleProvisioning} onApplyProvisioning={applyOracleProvisioning} onCancelProvisioning={() => setIdentityProvisioningPreview(null)} />
          : <WorkspaceLoading label="Access Control" message="Preparing users and role assignments…" />
      ) : activeView === "jobs" ? (
        jobsActivity
          ? <JobsActivityWorkspace data={jobsActivity} detail={jobDetail} busyExecutionId={busyExecutionId} csrfToken={bootstrap.csrf_token} allowRecovery={Boolean(bootstrap.user?.permissions.includes("operation.execute"))} onInspect={inspectJob} onRecoveryStarted={openRecoveredExecution} onCloseDetail={() => setJobDetail(null)} />
          : <WorkspaceLoading label="Jobs & Activity" message="Preparing retained execution history…" />
      ) : activeView === "operations" ? (
        operations
          ? <OperationsWorkspace
              data={operations}
              csrfToken={bootstrap.csrf_token}
              canManageCatalog={bootstrap.user?.permissions.includes("catalog.manage") ?? false}
              currentUsername={bootstrap.user?.username ?? ""}
              canManageUsers={bootstrap.user?.permissions.includes("user.manage") ?? false}
            />
          : <WorkspaceLoading label="Operations" message="Preparing your available Oracle EPM services..." />
      ) : activeView === "schedules" ? (
        <Suspense fallback={<WorkspaceLoading label="Schedules" message="Preparing governed automation schedules..." />}>
          <SchedulingWorkspace key={scheduleRefreshKey} csrfToken={bootstrap.csrf_token} onOpenExecution={openScheduledExecution} />
        </Suspense>
      ) : activeView === "data-review" ? (
        <DataReviewWorkspace csrfToken={bootstrap.csrf_token} canSaveViews={bootstrap.user?.permissions.includes("report.generate") ?? false} />
      ) : activeView === "reports" ? (
        <ReportGenerationRunner csrfToken={bootstrap.csrf_token} onBack={() => { window.location.hash = "#home"; }} />
      ) : activeView === "assistant" ? (
        <EpmAssistantWorkspace csrfToken={bootstrap.csrf_token} />
      ) : (
        <Dashboard
          bootstrap={bootstrap}
          home={home}
          busyTaskId={busyTaskId}
          health={health}
          healthBusy={healthBusy}
          onCheckHealth={checkHealth}
          onTaskStatus={updateTask}
          onSubmitApproval={submitForApproval}
        />
      )}
    </AppShell>
  );
}

function message(reason: unknown) {
  if (reason instanceof ApiError || reason instanceof Error) return reason.message;
  return "An unexpected error occurred. Please try again.";
}

function viewFromHash(): AppView {
  const hash = window.location.hash.toLowerCase();
  if (hash === "#tasks") return "tasks";
  if (hash === "#cycles") return "cycles";
  if (hash === "#approvals") return "approvals";
  if (hash === "#notifications") return "notifications";
  if (hash === "#access") return "access";
  if (hash === "#jobs") return "jobs";
  if (hash === "#operations") return "operations";
  if (hash === "#schedules") return "schedules";
  if (hash === "#data-review") return "data-review";
  if (hash === "#reports") return "reports";
  if (hash === "#assistant") return "assistant";
  return "home";
}
