import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { BootstrapResponse, HomeResponse } from "./api/types";

const bootstrap: BootstrapResponse = {
  product: { name: "BISP EPM Automation", company: "BISP Solutions", api_version: "v1" },
  authenticated: true,
  requires_bootstrap: false,
  csrf_token: "test-csrf",
  identity_authentication: {
    federated_enabled: false,
    oracle_credentials_enabled: false,
    provider_name: "Oracle Cloud Identity",
    login_url: null,
    local_recovery_enabled: true
  },
  environment: {
    application_name: "Vision",
    deployment_mode: "cloud",
    base_url: "https://example.oraclecloud.com",
    configured: true
  },
  user: {
    user_id: 7,
    username: "planner",
    display_name: "Finance Planner",
    email: "planner@example.com",
    platform_roles: ["USER"],
    permissions: ["process.run"],
    persona: "USER",
    persona_label: "User / Planner"
  },
  navigation: [
    { code: "home", label: "Home", path: "/", group: "Workspace" },
    { code: "tasks", label: "My Work", path: "#tasks", group: "Workspace" }
  ],
  features: { legacy_ui: true, task_engine: true, planning_cycles: true, approvals: true, notifications: true, access_control: true, jobs_activity: true }
};

const home: HomeResponse = {
  status: "ok",
  summary: { action_required: 1, due_today: 1, overdue: 0, completed: 0 },
  cycles: [{
    cycle_id: 11,
    code: "FCST-FY27-08",
    name: "August Forecast",
    cycle_type: "FORECAST",
    process_code: "MONTHLY_FORECAST",
    scenario: "Forecast",
    year: "FY27",
    actual_through_period: "Jul",
    forecast_start_period: "Aug",
    start_date: "2026-08-01",
    due_date: "2026-08-10",
    status: "IN_PROGRESS",
    completed_at: null,
    completed_stages: 0,
    stage_count: 1,
    progress_percent: 0,
    current_stage: {
      stage_id: 21,
      cycle_id: 11,
      sequence: 1,
      code: "INPUT",
      name: "Enter forecast",
      status: "IN_PROGRESS",
      start_date: "2026-08-01",
      due_date: "2026-08-05",
      completed_at: null
    },
    stages: []
  }],
  tasks: [{
    task_id: 31,
    stage_id: 21,
    cycle_id: 11,
    cycle_code: "FCST-FY27-08",
    cycle_name: "August Forecast",
    stage_code: "INPUT",
    stage_name: "Enter forecast",
    title: "Review revenue assumptions",
    description: "Confirm the current driver assumptions.",
    task_type: "DATA_REVIEW",
    status: "NOT_STARTED",
    readiness: "READY",
    priority: "HIGH",
    assigned_user_id: 7,
    assigned_role_code: null,
    entity: "Sales East",
    scenario: "Forecast",
    period: "Aug",
    due_at: "2026-08-09T12:00:00Z",
    action_type: "OPEN_DATA_REVIEW",
    action_config: {},
    dependency_ids: [],
    incomplete_dependency_ids: [],
    completed_at: null,
    execution_attempts: []
  }],
  recent_activity: []
};

const notificationInbox = { status: "success", unread_count: 0, notifications: [] };

function response(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  }));
}

describe("App", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/#home");
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(bootstrap);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      return response({ detail: "Unexpected request" }, 404);
    }));
  });

  it("shows the planner's priorities and active cycle", async () => {
    render(<App />);

    expect(await screen.findByText("What needs your attention")).toBeTruthy();
    expect(screen.getByText("Review revenue assumptions")).toBeTruthy();
    expect(screen.getAllByText("August Forecast").length).toBeGreaterThan(0);
  });

  it("starts a ready task and refreshes the homepage", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(bootstrap);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/planning-tasks/31/status") return response({ status: "ok" });
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Start task" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/planning-tasks/31/status",
      expect.objectContaining({ method: "PATCH" })
    ));
  });

  it("opens the consolidated My Work workspace", async () => {
    window.location.hash = "#tasks";
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(bootstrap);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/planning-tasks") return response({
        status: "success",
        cycles: home.cycles,
        tasks: home.tasks
      });
      return response({ detail: "Unexpected request" }, 404);
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Move Planning forward" })).toBeTruthy();
    expect(screen.getByText("1 task shown")).toBeTruthy();
    expect(screen.getByText("Review revenue assumptions")).toBeTruthy();
    expect(screen.getByText("No unresolved dependencies")).toBeTruthy();
  });

  it("routes assigned Oracle operations into the modern runner", async () => {
    window.location.hash = "#tasks";
    const pipelineTask = {
      ...home.tasks[0],
      title: "Reconcile actual data",
      action_type: "RUN_PIPELINE"
    };
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(bootstrap);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/planning-tasks") return response({
        status: "success",
        cycles: home.cycles,
        tasks: [pipelineTask]
      });
      return response({ detail: "Unexpected request" }, 404);
    });

    render(<App />);

    const link = await screen.findByRole("link", { name: /Run operation/ });
    expect(link.getAttribute("href")).toBe(
      "/?operation=pipelines&planning_task_id=31#operations"
    );
  });

  it("guides an administrator through opening a Planning cycle", async () => {
    window.location.hash = "#cycles";
    const administrator = {
      ...bootstrap,
      user: {
        ...bootstrap.user!,
        username: "admin",
        display_name: "Planning Administrator",
        platform_roles: ["SERVICE_ADMINISTRATOR"],
        permissions: ["process.design"],
        persona: "SERVICE_ADMINISTRATOR" as const,
        persona_label: "Service Administrator"
      },
      navigation: [...bootstrap.navigation, { code: "cycles", label: "Planning Cycles", path: "#cycles", group: "Administration" }]
    };
    const catalog = {
      status: "success",
      cycles: [],
      users: [{ user_id: 7, username: "admin", display_name: "Planning Administrator", roles: ["SERVICE_ADMINISTRATOR"] }],
      roles: [
        { code: "USER", name: "User", description: "Completes assigned Planning work." },
        { code: "SERVICE_ADMINISTRATOR", name: "Service Administrator", description: "Administers the platform." },
        { code: "POWER_USER", name: "Power User", description: "Runs approved processes." }
      ]
    };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(administrator);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/planning-cycle-administration") return response(catalog);
      if (url === "/api/v1/planning-cycles" && init?.method === "POST") return response({ status: "success", cycle: home.cycles[0] }, 201);
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Open new cycle" }));
    fireEvent.change(screen.getByLabelText(/^Cycle name/), { target: { value: "March Forecast FY27" } });
    fireEvent.change(screen.getByLabelText(/^Planning year/), { target: { value: "FY27" } });
    fireEvent.click(screen.getByRole("button", { name: /Continue/ }));

    expect(await screen.findByRole("heading", { name: "Define stages and accountable owners" })).toBeTruthy();
    expect(screen.getAllByRole("option", { name: "Update substitution variables" }).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /Continue/ }));
    expect(await screen.findByRole("heading", { name: "Review before opening" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Open cycle" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/planning-cycles",
      expect.objectContaining({
        method: "POST",
        body: expect.stringContaining('"year":"FY27"')
      })
    ));
  });

  it("lets an assigned manager approve submitted Planning work", async () => {
    window.location.hash = "#approvals";
    const approval = {
      approval_id: 91,
      cycle_id: 11,
      cycle_name: "August Forecast",
      submitted_task_id: 31,
      submitted_task_title: "Submit Commercial Vehicles forecast",
      approval_task_id: 32,
      approval_task_title: "Review Commercial Vehicles forecast",
      entity: "Commercial Vehicles",
      scenario: "Forecast",
      period: "Aug",
      status: "PENDING",
      submitted_by_user_id: 8,
      submitted_by_name: "Finance Planner",
      submitted_at: "2026-08-08T08:00:00Z",
      decided_by_user_id: null,
      decided_at: null,
      decision_comment: null
    };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(bootstrap);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/planning-approvals" && !init?.method) return response({ status: "success", approvals: [approval] });
      if (url === "/api/v1/planning-approvals/91" && init?.method === "PATCH") return response({ status: "success" });
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Approvals" })).toBeTruthy();
    expect(screen.getByText("Commercial Vehicles")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/planning-approvals/91",
      expect.objectContaining({ method: "PATCH", body: expect.stringContaining('"decision":"APPROVED"') })
    ));
  });

  it("shows meaningful user notifications", async () => {
    window.location.hash = "#notifications";
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(bootstrap);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response({ status: "success", unread_count: 1, notifications: [{ notification_id: 4, event_type: "APPROVAL_RETURNED", severity: "WARNING", title: "Planning submission returned", message: "Update the volume assumption.", action_url: "#tasks", source_type: "PLANNING_APPROVAL", source_id: "91", created_at: "2026-08-08T09:00:00Z", read_at: null }] });
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Notifications" })).toBeTruthy();
    expect(screen.getByText("Planning submission returned")).toBeTruthy();
    expect(screen.getByText("Update the volume assumption.")).toBeTruthy();
  });

  it("shows failed job evidence without exposing a technical history page", async () => {
    window.location.hash = "#jobs";
    const administrator = {
      ...bootstrap,
      user: {
        ...bootstrap.user!,
        platform_roles: ["SERVICE_ADMINISTRATOR"],
        permissions: ["history.view"],
        persona: "SERVICE_ADMINISTRATOR" as const,
        persona_label: "Service Administrator"
      },
      navigation: [...bootstrap.navigation, { code: "jobs", label: "Jobs & Activity", path: "#jobs", group: "Automation" }]
    };
    const job = {
      execution_id: "job-test-001",
      name: "Load July Actuals",
      status: "FAILED",
      started_at: "2026-08-08T08:00:00Z",
      completed_at: "2026-08-08T08:00:45Z",
      duration_seconds: 45,
      completed_steps: 1,
      total_steps: 1,
      initiated_by: "Planning Administrator",
      trigger_source: "MANUAL",
      error_message: "Oracle rejected 3 records."
    };
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(administrator);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/jobs") return response({ status: "success", summary: { total: 1, running: 0, successful: 0, failed: 1, success_rate: 0 }, jobs: [job] });
      if (url === "/api/v1/jobs/job-test-001") return response({ status: "success", job: { ...job, record_statistics: { source: "ORACLE_JOB_DETAILS", records_read: 100, records_processed: 97, records_rejected: 3, details: [] }, steps: [{ sequence: 1, name: "Import data", status: "FAILED", started_at: job.started_at, completed_at: job.completed_at, duration_seconds: 45, details: { job_id: 917 }, error_message: "Invalid members." }] } });
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Jobs & Activity" })).toBeTruthy();
    expect(screen.getByText("Load July Actuals")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "View details" }));
    expect(await screen.findByRole("heading", { name: "Load July Actuals" })).toBeTruthy();
    expect(screen.getByText("Invalid members.")).toBeTruthy();
    expect(screen.getByText("Records processed")).toBeTruthy();
    expect(screen.getByText("97")).toBeTruthy();
  });

  it("lets authorized users choose and open an individual service", async () => {
    window.location.hash = "#operations";
    const administrator = {
      ...bootstrap,
      user: {
        ...bootstrap.user!,
        platform_roles: ["SERVICE_ADMINISTRATOR"],
        permissions: ["operation.execute", "catalog.manage"],
        persona: "SERVICE_ADMINISTRATOR" as const,
        persona_label: "Service Administrator"
      },
      navigation: [...bootstrap.navigation, { code: "operations", label: "Operations", path: "#operations", group: "Automation" }]
    };
    const catalog = {
      status: "success",
      operations: [
        { code: "business-rules", display_name: "Business Rules", description: "Run a deployed calculation with optional runtime prompts.", category: "Calculation", risk_level: "Controlled", route: "/app/operations/business-rules" },
        { code: "data-integrations", display_name: "Data Integrations", description: "Load governed file-based data.", category: "Data loading", risk_level: "Elevated", route: "/app/operations/data-integrations" }
      ]
    };
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(administrator);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/operations") return response(catalog);
      if (url === "/api/v1/operations/oracle-catalog" && !init?.method) return response({
        status: "success",
        environment: {
          application_name: "Vision",
          deployment_mode: "cloud",
          base_url: "https://example.oraclecloud.com",
          configured: true
        },
        summary: { verified: 2, attention: 0, last_synchronized_at: "2026-08-11T10:00:00Z" },
        artifacts: []
      });
      if (url === "/api/v1/operations/oracle-catalog/sync" && init?.method === "POST") return response({
        status: "success",
        sync: {
          environment_key: "env-1", application_name: "Vision", oracle_available: true,
          verified_pipelines: 0, missing_pipelines: 0, discovered_integrations: 0,
          verified_integrations: 0, missing_integrations: 0, pending_integrations: 0,
          verification_errors: 0, verified_business_rules: 1, verified_data_maps: 1,
          verified_metadata_jobs: 0, verified_data_import_jobs: 0,
          verified_cube_refresh_jobs: 0, verified_cubes: 1, total_verified: 3,
          total_hidden: 0, synchronized_at: "2026-08-11T10:05:00Z",
          message: "Oracle catalog synchronized for 'Vision'."
        },
        artifacts: []
      });
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Operations" })).toBeTruthy();
    expect(screen.getByText("Business Rules")).toBeTruthy();
    expect(screen.getByText("Data Integrations")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /Open service/ })).toHaveLength(2);
    expect(await screen.findByText("current artifacts")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Sync from Oracle/ }));
    expect(await screen.findByText("Catalog synchronized")).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText("Search by service or purpose"), { target: { value: "data" } });
    expect(screen.queryByText("Business Rules")).toBeNull();
    expect(screen.getByText("Data Integrations")).toBeTruthy();
  });

  it("runs a Business Rule through prepare, review, and monitored result", async () => {
    window.location.hash = "#operations";
    const administrator = {
      ...bootstrap,
      user: {
        ...bootstrap.user!,
        platform_roles: ["SERVICE_ADMINISTRATOR"],
        permissions: ["operation.execute"],
        persona: "SERVICE_ADMINISTRATOR" as const,
        persona_label: "Service Administrator"
      },
      navigation: [...bootstrap.navigation, { code: "operations", label: "Operations", path: "#operations", group: "Automation" }]
    };
    const operations = {
      status: "success",
      operations: [{ code: "business-rules", display_name: "Business Rules", description: "Run a deployed calculation with optional runtime prompts.", category: "Calculation", risk_level: "Controlled", route: "/app/operations/business-rules" }]
    };
    const execution = {
      execution_id: "rule-execution-1",
      operation_name: "Business Rule - Calculate Revenue",
      status: "SUCCESS",
      started_at: "2026-08-08T10:00:00Z",
      completed_at: "2026-08-08T10:00:03Z",
      error_message: null,
      initiated_by: "Planning Administrator",
      trigger_source: "MANUAL",
      steps: [{ name: "Run Business Rule", sequence: 1, status: "SUCCESS", started_at: "2026-08-08T10:00:00Z", completed_at: "2026-08-08T10:00:03Z", details: {}, error_message: null }],
      completed_steps: 1,
      total_steps: 1,
      artifacts: [],
      log_url: "/app/operations/runs/rule-execution-1/log",
      terminal: true
    };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(administrator);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/operations") return response(operations);
      if (url === "/api/v1/operations/business-rules/catalog") return response({
        status: "success",
        jobs: ["Calculate Revenue"],
        rtp_registry: {
          health: "EMPTY",
          application_name: "Vision",
          live_catalog_available: true,
          live_rule_count: 1,
          synchronized_rule_count: 0,
          synchronized_prompt_count: 0,
          unsynchronized_live_rules: ["Calculate Revenue"],
          definitions_not_in_live_catalog: [],
          definitions: [],
          recent_syncs: []
        }
      });
      if (url === "/api/v1/operations/business-rules/runs" && init?.method === "POST") return response({ status: "accepted", execution_id: "rule-execution-1", redirect: "/app/operations/runs/rule-execution-1" }, 202);
      if (url === "/api/v1/operations/runs/rule-execution-1") return response(execution);
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Open service/ }));
    expect(await screen.findByText("Registry health")).toBeTruthy();
    expect(screen.getByText("1", { selector: ".rtp-registry__summary strong" })).toBeTruthy();
    const ruleSelect = await screen.findByRole("combobox");
    fireEvent.change(ruleSelect, { target: { value: "Calculate Revenue" } });
    fireEvent.click(await screen.findByRole("button", { name: "Add input" }));
    fireEvent.change(screen.getByLabelText("Prompt name"), { target: { value: "Year" } });
    fireEvent.change(screen.getByLabelText("Member or value"), { target: { value: "FY27" } });
    fireEvent.click(screen.getByRole("button", { name: /Review execution/ }));

    expect(await screen.findByRole("heading", { name: "Review before execution" })).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /Start operation/ }));

    expect(await screen.findByRole("heading", { name: "Operation completed successfully" })).toBeTruthy();
    const submitted = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/operations/business-rules/runs");
    expect(submitted).toBeTruthy();
    expect(JSON.parse(String(submitted?.[1]?.body))).toEqual({ rule_name: "Calculate Revenue", runtime_prompts: { Year: "FY27" } });
  });

  it("requires review of Data Map overrides and target clearing", async () => {
    window.location.hash = "#operations";
    const administrator = {
      ...bootstrap,
      user: { ...bootstrap.user!, platform_roles: ["SERVICE_ADMINISTRATOR"], permissions: ["operation.execute"], persona: "SERVICE_ADMINISTRATOR" as const, persona_label: "Service Administrator" },
      navigation: [...bootstrap.navigation, { code: "operations", label: "Operations", path: "#operations", group: "Automation" }]
    };
    const operation = { code: "data-maps", display_name: "Data Maps", description: "Move approved Planning data.", category: "Data movement", risk_level: "Elevated", route: "/app/operations/data-maps" };
    const execution = { execution_id: "map-execution-1", operation_name: "Data Map - Revenue to Reporting", status: "SUCCESS", started_at: "2026-08-08T10:00:00Z", completed_at: "2026-08-08T10:00:03Z", error_message: null, initiated_by: "Planning Administrator", trigger_source: "MANUAL", steps: [], completed_steps: 1, total_steps: 1, artifacts: [], log_url: null, terminal: true };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(administrator);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/operations") return response({ status: "success", operations: [operation] });
      if (url === "/api/v1/operations/data-maps/catalog") return response({ status: "success", jobs: ["Revenue to Reporting"] });
      if (url === "/api/v1/operations/data-maps/runs" && init?.method === "POST") return response({ status: "accepted", execution_id: execution.execution_id, redirect: `/app/operations/runs/${execution.execution_id}` }, 202);
      if (url === `/api/v1/operations/runs/${execution.execution_id}`) return response(execution);
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Open service/ }));
    fireEvent.change(await screen.findByRole("combobox"), { target: { value: "Revenue to Reporting" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Add input" }));
    fireEvent.change(screen.getByLabelText("Dimension"), { target: { value: "Year" } });
    fireEvent.change(screen.getByLabelText("Member selection"), { target: { value: "FY27" } });
    fireEvent.click(screen.getByRole("button", { name: /Review execution/ }));

    expect(await screen.findByText("Target clearing is enabled")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /Start operation/ }));
    expect(await screen.findByRole("heading", { name: "Operation completed successfully" })).toBeTruthy();

    const submitted = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/operations/data-maps/runs");
    expect(JSON.parse(String(submitted?.[1]?.body))).toEqual({ data_map_name: "Revenue to Reporting", clear_target: true, member_overrides: { Year: "FY27" }, exclusion_overrides: {} });
  });

  it("uploads the selected current file before starting a Data Integration", async () => {
    window.location.hash = "#operations";
    const administrator = {
      ...bootstrap,
      user: { ...bootstrap.user!, platform_roles: ["SERVICE_ADMINISTRATOR"], permissions: ["operation.execute"], persona: "SERVICE_ADMINISTRATOR" as const, persona_label: "Service Administrator" },
      navigation: [...bootstrap.navigation, { code: "operations", label: "Operations", path: "#operations", group: "Automation" }]
    };
    const operation = { code: "data-integrations", display_name: "Data Integrations", description: "Load governed file-based data.", category: "Data loading", risk_level: "Elevated", route: "/app/operations/data-integrations" };
    const execution = { execution_id: "integration-execution-1", operation_name: "Data Integration - Forecast Load", status: "SUCCESS", started_at: "2026-08-09T10:00:00Z", completed_at: "2026-08-09T10:00:06Z", error_message: null, initiated_by: "Planning Administrator", trigger_source: "MANUAL", steps: [], completed_steps: 1, total_steps: 1, artifacts: [], log_url: null, terminal: true };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(administrator);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/operations") return response({ status: "success", operations: [operation] });
      if (url === "/api/v1/operations/data-integrations/catalog") return response({ status: "success", integrations: [{ name: "Forecast Load", description: "Monthly forecast" }] });
      if (url === "/api/v1/data-review/cubes") return response({ status: "success", cubes: [{ name: "Plan1", cube_name: "Plan1", cube_type: 1, dimension_count: 8 }] });
      if (url === "/api/v1/data-review/cubes/Plan1/dimensions") return response({ status: "success", cube: "Plan1", dimensions: [{ name: "Years", dimension_type: "Year" }, { name: "Period", dimension_type: "Period" }] });
      if (url.startsWith("/api/v1/data-review/cubes/Plan1/dimensions/Years/members")) return response({ status: "success", cube: "Plan1", dimension: "Years", query: "", members: [{ name: "FY27", alias: null, path: null, parent_name: null, has_children: false }], total_matches: 1, has_more: false, offset: 0, limit: 100 });
      if (url.startsWith("/api/v1/data-review/cubes/Plan1/dimensions/Period/members")) return response({ status: "success", cube: "Plan1", dimension: "Period", query: "", members: ["Jan", "Mar"].map((name) => ({ name, alias: null, path: null, parent_name: null, has_children: false })), total_matches: 2, has_more: false, offset: 0, limit: 100 });
      if (url.startsWith("/api/v1/uploads?filename=")) return response({ status: "success", upload: { token: "upload-token-123", filename: "Forecast.csv", size: 24 } });
      if (url === "/api/v1/operations/data-integrations/runs" && init?.method === "POST") return response({ status: "accepted", execution_id: execution.execution_id, redirect: `/app/operations/runs/${execution.execution_id}` }, 202);
      if (url === `/api/v1/operations/runs/${execution.execution_id}`) return response(execution);
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Open service/ }));
    const integrationSelects = await screen.findAllByRole("combobox");
    expect(screen.getByRole("radio", { name: /Use configured file/ })).toBeTruthy();
    fireEvent.change(integrationSelects[0], { target: { value: "Forecast Load" } });
    await screen.findByRole("option", { name: "FY27" });
    fireEvent.change(screen.getByLabelText("Planning year"), { target: { value: "FY27" } });
    fireEvent.change(screen.getByLabelText("Start period"), { target: { value: "Jan" } });
    fireEvent.change(screen.getByLabelText("End period"), { target: { value: "Mar" } });
    const file = new File(["Account,Jan\nRevenue,100"], "Forecast.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("Local data file"), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText("Oracle upload target"), { target: { value: "inbox/monthly/Forecast.csv" } });
    fireEvent.click(screen.getByRole("button", { name: /Review execution/ }));

    expect(await screen.findByText("Exact Oracle target will be replaced")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /Start Data Integration/ }));
    expect(await screen.findByRole("heading", { name: "Operation completed successfully" })).toBeTruthy();

    const upload = fetchMock.mock.calls.find(([url]) => String(url).startsWith("/api/v1/uploads?filename="));
    expect(new Headers(upload?.[1]?.headers).get("Content-Type")).toBe("application/octet-stream");
    const submitted = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/operations/data-integrations/runs");
    expect(JSON.parse(String(submitted?.[1]?.body))).toEqual({ integration_name: "Forecast Load", start_period: "Jan#FY27", end_period: "Mar#FY27", import_mode: "Replace", export_mode: "Merge", upload_target: "inbox/monthly/Forecast.csv", upload_token: "upload-token-123", inbox_file: null });
  });

  it("runs Data Integration with a selected live Oracle Inbox file", async () => {
    window.location.hash = "#operations";
    const administrator = {
      ...bootstrap,
      user: { ...bootstrap.user!, platform_roles: ["SERVICE_ADMINISTRATOR"], permissions: ["operation.execute"], persona: "SERVICE_ADMINISTRATOR" as const, persona_label: "Service Administrator" },
      navigation: [...bootstrap.navigation, { code: "operations", label: "Operations", path: "#operations", group: "Automation" }]
    };
    const operation = { code: "data-integrations", display_name: "Data Integrations", description: "Load governed file-based data.", category: "Data loading", risk_level: "Elevated", route: "/app/operations/data-integrations" };
    const execution = { execution_id: "integration-inbox-1", operation_name: "Data Integration - Forecast Load", status: "SUCCESS", started_at: "2026-08-11T10:00:00Z", completed_at: "2026-08-11T10:00:06Z", error_message: null, initiated_by: "Planning Administrator", trigger_source: "MANUAL", steps: [], completed_steps: 1, total_steps: 1, artifacts: [], log_url: null, terminal: true };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(administrator);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/operations") return response({ status: "success", operations: [operation] });
      if (url === "/api/v1/operations/data-integrations/catalog") return response({ status: "success", integrations: [{ name: "Forecast Load", description: "Monthly forecast" }] });
      if (url === "/api/v1/data-review/cubes") return response({ status: "success", cubes: [{ name: "Plan1", cube_name: "Plan1", cube_type: 1, dimension_count: 8 }] });
      if (url === "/api/v1/data-review/cubes/Plan1/dimensions") return response({ status: "success", cube: "Plan1", dimensions: [{ name: "Years", dimension_type: "Year" }, { name: "Period", dimension_type: "Period" }] });
      if (url.startsWith("/api/v1/data-review/cubes/Plan1/dimensions/Years/members")) return response({ status: "success", cube: "Plan1", dimension: "Years", query: "", members: [{ name: "FY27", alias: null, path: null, parent_name: null, has_children: false }], total_matches: 1, has_more: false, offset: 0, limit: 100 });
      if (url.startsWith("/api/v1/data-review/cubes/Plan1/dimensions/Period/members")) return response({ status: "success", cube: "Plan1", dimension: "Period", query: "", members: [{ name: "Aug", alias: null, path: null, parent_name: null, has_children: false }], total_matches: 1, has_more: false, offset: 0, limit: 100 });
      if (url === "/api/v1/operations/files/catalog?purpose=data-integration") return response({
        status: "success",
        purpose: "data-integration",
        files: [{ name: "#epminbox/Sales_Aug.csv", folder: "Inbox", file_type: "EXTERNAL", size_bytes: 2048, last_modified_epoch_ms: 1786420800000 }]
      });
      if (url === "/api/v1/operations/data-integrations/runs" && init?.method === "POST") return response({ status: "accepted", execution_id: execution.execution_id, redirect: `/app/operations/runs/${execution.execution_id}` }, 202);
      if (url === `/api/v1/operations/runs/${execution.execution_id}`) return response(execution);
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Open service/ }));
    const integrationSelects = await screen.findAllByRole("combobox");
    fireEvent.change(integrationSelects[0], { target: { value: "Forecast Load" } });
    await screen.findByRole("option", { name: "FY27" });
    fireEvent.change(screen.getByLabelText("Planning year"), { target: { value: "FY27" } });
    fireEvent.change(screen.getByLabelText("Start period"), { target: { value: "Aug" } });
    fireEvent.change(screen.getByLabelText("End period"), { target: { value: "Aug" } });
    fireEvent.click(screen.getByRole("radio", { name: /Choose from Oracle Inbox/ }));
    const filePicker = await screen.findByLabelText("Existing Oracle integration file");
    await waitFor(() => expect((filePicker as HTMLSelectElement).value).toBe("#epminbox/Sales_Aug.csv"));
    fireEvent.click(screen.getByRole("button", { name: /Review execution/ }));

    expect(await screen.findByRole("heading", { name: "Review the Data Integration" })).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /Start Data Integration/ }));
    expect(await screen.findByRole("heading", { name: "Operation completed successfully" })).toBeTruthy();

    const submitted = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/operations/data-integrations/runs");
    expect(JSON.parse(String(submitted?.[1]?.body))).toEqual({ integration_name: "Forecast Load", start_period: "Aug#FY27", end_period: "Aug#FY27", import_mode: "Replace", export_mode: "Merge", upload_token: null, inbox_file: "#epminbox/Sales_Aug.csv" });
    expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/v1/uploads?filename="))).toBe(false);
  });

  it("inspects live Pipeline inputs, uploads required files, and monitors the run", async () => {
    window.location.hash = "#operations";
    const administrator = {
      ...bootstrap,
      user: { ...bootstrap.user!, platform_roles: ["SERVICE_ADMINISTRATOR"], permissions: ["operation.execute"], persona: "SERVICE_ADMINISTRATOR" as const, persona_label: "Service Administrator" },
      navigation: [...bootstrap.navigation, { code: "operations", label: "Operations", path: "#operations", group: "Automation" }]
    };
    const operation = { code: "pipelines", display_name: "Pipelines", description: "Run a multi-stage Oracle Pipeline.", category: "Orchestration", risk_level: "Elevated", route: "/app/operations/pipelines" };
    const preview = {
      code: "PIPE01",
      display_name: "Monthly Forecast",
      variables: [
        { name: "STARTPERIOD", display_name: "Start Period", default_value: "Jan-27", required: true, editable: true },
        { name: "CURRENTYEAR", display_name: "Planning Year", default_value: "FY27", required: false, editable: true }
      ],
      file_requirements: [{ key: "DataFile", display_name: "Forecast data file", configured_reference: null, required: true, allowed_extensions: [".csv"], consumers: ["Load Forecast"] }],
      stages: [{ name: "LOAD", display_name: "Load forecast data", job_count: 1, runs_in_parallel: false }, { name: "CALCULATE", display_name: "Calculate forecast", job_count: 2, runs_in_parallel: true }]
    };
    const execution = { execution_id: "pipeline-execution-1", operation_name: "Pipeline - PIPE01", status: "SUCCESS", started_at: "2026-08-09T10:00:00Z", completed_at: "2026-08-09T10:00:12Z", error_message: null, initiated_by: "Planning Administrator", trigger_source: "MANUAL", steps: [], completed_steps: 3, total_steps: 3, artifacts: [], log_url: null, terminal: true };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(administrator);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/operations") return response({ status: "success", operations: [operation] });
      if (url === "/api/v1/operations/pipelines/catalog") return response({
        status: "success",
        pipelines: [
          { code: "PIPE01", name: "Monthly Forecast", description: "Monthly lifecycle" },
          { code: "PL02", name: "Removed Pipeline", description: "No longer in Oracle" }
        ],
        artifacts: [
          { oracle_identifier: "PIPE01", is_verified: true, is_active: true, status: "VERIFIED" },
          { oracle_identifier: "PL02", is_verified: false, is_active: true, status: "MISSING" }
        ]
      });
      if (url === "/api/v1/operations/pipelines/PIPE01/preflight") return response({ status: "success", preview });
      if (url.startsWith("/api/v1/uploads?filename=")) return response({ status: "success", upload: { token: "pipeline-upload-123", filename: "Forecast.csv", size: 24 } });
      if (url === "/api/v1/operations/pipelines/runs" && init?.method === "POST") return response({ status: "accepted", execution_id: execution.execution_id, redirect: `/app/operations/runs/${execution.execution_id}` }, 202);
      if (url === `/api/v1/operations/runs/${execution.execution_id}`) return response(execution);
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Open service/ }));
    expect(screen.queryByRole("option", { name: /Removed Pipeline/ })).toBeNull();
    fireEvent.change(await screen.findByLabelText("Oracle Pipeline"), { target: { value: "PIPE01" } });
    fireEvent.click(screen.getByRole("button", { name: /Inspect live definition/ }));

    expect(await screen.findByText("Calculate forecast")).toBeTruthy();
    expect((screen.getByLabelText("Start Period") as HTMLSelectElement).tagName).toBe("SELECT");
    expect((screen.getByLabelText("Planning Year") as HTMLSelectElement).tagName).toBe("SELECT");
    fireEvent.change(screen.getByLabelText("Start Period"), { target: { value: "Feb-27" } });
    fireEvent.change(screen.getByLabelText("Planning Year"), { target: { value: "FY28" } });
    const file = new File(["Account,Feb\nRevenue,125"], "Forecast.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("Forecast data file local file"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: /Review Pipeline run/ }));

    expect(await screen.findByRole("heading", { name: "Review the complete Pipeline run" })).toBeTruthy();
    expect(screen.getByText("2 jobs · Parallel")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /Start Oracle Pipeline/ }));
    expect(await screen.findByRole("heading", { name: "Operation completed successfully" })).toBeTruthy();

    const submitted = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/operations/pipelines/runs");
    expect(JSON.parse(String(submitted?.[1]?.body))).toEqual({ pipeline_code: "PIPE01", variables: { STARTPERIOD: "Feb-27", CURRENTYEAR: "FY28" }, uploads: { DataFile: "pipeline-upload-123" }, inbox_files: {} });
  });

  it("runs a saved Planning Data Import with a reviewed replacement file", async () => {
    window.location.hash = "#operations";
    const administrator = {
      ...bootstrap,
      user: { ...bootstrap.user!, platform_roles: ["SERVICE_ADMINISTRATOR"], permissions: ["operation.execute"], persona: "SERVICE_ADMINISTRATOR" as const, persona_label: "Service Administrator" },
      navigation: [...bootstrap.navigation, { code: "operations", label: "Operations", path: "#operations", group: "Automation" }]
    };
    const operation = { code: "data-import", display_name: "Planning Data Import", description: "Load a compatible file with a saved native Planning job.", category: "Data loading", risk_level: "Elevated", route: "/app/operations/data-import" };
    const execution = { execution_id: "data-import-execution-1", operation_name: "Planning Data Import - Import Forecast Data", status: "SUCCESS", started_at: "2026-08-09T10:00:00Z", completed_at: "2026-08-09T10:00:09Z", error_message: null, initiated_by: "Planning Administrator", trigger_source: "MANUAL", steps: [], completed_steps: 1, total_steps: 1, artifacts: [], log_url: null, terminal: true };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(administrator);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/operations") return response({ status: "success", operations: [operation] });
      if (url === "/api/v1/operations/data-import/catalog") return response({ status: "success", jobs: ["Import Forecast Data"] });
      if (url.startsWith("/api/v1/uploads?filename=")) return response({ status: "success", upload: { token: "data-import-upload-123", filename: "Forecast_Data.csv", size: 28 } });
      if (url === "/api/v1/operations/data-import/runs" && init?.method === "POST") return response({ status: "accepted", execution_id: execution.execution_id, redirect: `/app/operations/runs/${execution.execution_id}` }, 202);
      if (url === `/api/v1/operations/runs/${execution.execution_id}`) return response(execution);
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Open service/ }));
    expect(screen.getByRole("radio", { name: /Use Oracle job file/ })).toBeTruthy();
    fireEvent.change(await screen.findByLabelText("Import Data job"), { target: { value: "Import Forecast Data" } });
    fireEvent.change(screen.getByLabelText("Error output filename"), { target: { value: "Forecast_Errors.log" } });
    const file = new File(["Account,Jan\nRevenue,100"], "Forecast_Data.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("Local Planning data file"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: /Review Data Import/ }));

    expect(await screen.findByRole("heading", { name: "Review the Planning Data Import" })).toBeTruthy();
    expect(screen.getByText("A matching Inbox filename will be replaced")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /Start Planning Data Import/ }));
    expect(await screen.findByRole("heading", { name: "Operation completed successfully" })).toBeTruthy();

    const submitted = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/operations/data-import/runs");
    expect(JSON.parse(String(submitted?.[1]?.body))).toEqual({ job_name: "Import Forecast Data", upload_token: "data-import-upload-123", inbox_file: null, error_file_name: "Forecast_Errors.log" });
  });

  it("runs Planning Data Import by selecting an existing Oracle Inbox file", async () => {
    window.location.hash = "#operations";
    const administrator = {
      ...bootstrap,
      user: { ...bootstrap.user!, platform_roles: ["SERVICE_ADMINISTRATOR"], permissions: ["operation.execute"], persona: "SERVICE_ADMINISTRATOR" as const, persona_label: "Service Administrator" },
      navigation: [...bootstrap.navigation, { code: "operations", label: "Operations", path: "#operations", group: "Automation" }]
    };
    const operation = { code: "data-import", display_name: "Planning Data Import", description: "Load a compatible file with a saved native Planning job.", category: "Data loading", risk_level: "Elevated", route: "/app/operations/data-import" };
    const execution = { execution_id: "data-import-inbox-1", operation_name: "Planning Data Import - Import Forecast Data", status: "SUCCESS", started_at: "2026-08-11T10:00:00Z", completed_at: "2026-08-11T10:00:06Z", error_message: null, initiated_by: "Planning Administrator", trigger_source: "MANUAL", steps: [], completed_steps: 1, total_steps: 1, artifacts: [], log_url: null, terminal: true };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(administrator);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/operations") return response({ status: "success", operations: [operation] });
      if (url === "/api/v1/operations/data-import/catalog") return response({ status: "success", jobs: ["Import Forecast Data"] });
      if (url === "/api/v1/operations/files/catalog?purpose=data-import") return response({
        status: "success",
        purpose: "data-import",
        files: [
          { name: "Forecast_Aug.csv", folder: "Inbox", file_type: "EXTERNAL", size_bytes: 2048, last_modified_epoch_ms: 1786420800000 },
          { name: "Forecast_Jul.csv", folder: "Inbox", file_type: "EXTERNAL", size_bytes: 1024, last_modified_epoch_ms: 1783742400000 }
        ]
      });
      if (url === "/api/v1/operations/data-import/runs" && init?.method === "POST") return response({ status: "accepted", execution_id: execution.execution_id, redirect: `/app/operations/runs/${execution.execution_id}` }, 202);
      if (url === `/api/v1/operations/runs/${execution.execution_id}`) return response(execution);
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Open service/ }));
    fireEvent.change(await screen.findByLabelText("Import Data job"), { target: { value: "Import Forecast Data" } });
    fireEvent.click(screen.getByRole("radio", { name: /Choose from Oracle Inbox/ }));
    const filePicker = await screen.findByLabelText("Existing Oracle Inbox file");
    fireEvent.change(filePicker, { target: { value: "Forecast_Aug.csv" } });
    fireEvent.click(screen.getByRole("button", { name: /Review Data Import/ }));

    expect(await screen.findByRole("heading", { name: "Review the Planning Data Import" })).toBeTruthy();
    expect(screen.getByText("Oracle Inbox")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /Start Planning Data Import/ }));
    expect(await screen.findByRole("heading", { name: "Operation completed successfully" })).toBeTruthy();

    const submitted = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/operations/data-import/runs");
    expect(JSON.parse(String(submitted?.[1]?.body))).toEqual({ job_name: "Import Forecast Data", upload_token: null, inbox_file: "Forecast_Aug.csv", error_file_name: null });
    expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/v1/uploads?filename="))).toBe(false);
  });

  it("runs Metadata Import with an optional successful-import Cube Refresh", async () => {
    window.history.replaceState(
      {},
      "",
      "/?operation=metadata-import&planning_task_id=47#operations"
    );
    const administrator = {
      ...bootstrap,
      user: { ...bootstrap.user!, platform_roles: ["SERVICE_ADMINISTRATOR"], permissions: ["operation.execute"], persona: "SERVICE_ADMINISTRATOR" as const, persona_label: "Service Administrator" },
      navigation: [...bootstrap.navigation, { code: "operations", label: "Operations", path: "#operations", group: "Automation" }]
    };
    const operation = { code: "metadata-import", display_name: "Metadata Import", description: "Import governed Planning metadata and optionally refresh the cube.", category: "Application administration", risk_level: "Elevated", route: "/app/operations/metadata-import" };
    const execution = { execution_id: "metadata-execution-1", operation_name: "Metadata Import - Import Products", status: "SUCCESS", started_at: "2026-08-10T10:00:00Z", completed_at: "2026-08-10T10:00:15Z", error_message: null, initiated_by: "Planning Administrator", trigger_source: "PLANNING_TASK", steps: [], completed_steps: 2, total_steps: 2, artifacts: [{ name: "Metadata_Errors.csv", url: "/api/v1/files/Metadata_Errors.csv" }], log_url: null, terminal: true };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(administrator);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/operations") return response({ status: "success", operations: [operation] });
      if (url === "/api/v1/operations/metadata-import/catalog") return response({ status: "success", jobs: ["Import Products"], refresh_jobs: ["Refresh_Cube"] });
      if (url.startsWith("/api/v1/uploads?filename=")) return response({ status: "success", upload: { token: "metadata-upload-123", filename: "Products.csv", size: 24 } });
      if (url === "/api/v1/operations/metadata-import/runs" && init?.method === "POST") return response({ status: "accepted", execution_id: execution.execution_id, redirect: `/app/operations/runs/${execution.execution_id}` }, 202);
      if (url === `/api/v1/operations/runs/${execution.execution_id}`) return response(execution);
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Metadata Import" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Use Oracle job files/ })).toBeTruthy();
    fireEvent.change(await screen.findByLabelText("Import Metadata job"), { target: { value: "Import Products" } });
    fireEvent.change(screen.getByLabelText("Metadata error output filename"), { target: { value: "Metadata_Errors.csv" } });
    const file = new File(["Product,Parent\niPhone,Phones"], "Products.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("Local metadata file"), { target: { files: [file] } });
    fireEvent.click(screen.getByLabelText(/Refresh the Planning cube/));
    fireEvent.change(screen.getByLabelText("Metadata Cube Refresh job"), { target: { value: "Refresh_Cube" } });
    fireEvent.click(screen.getByRole("button", { name: /Review Metadata Import/ }));

    expect(await screen.findByRole("heading", { name: "Review the Metadata Import" })).toBeTruthy();
    expect(screen.getByText("Cube Refresh is conditional")).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/I reviewed the saved job/));
    fireEvent.click(screen.getByRole("button", { name: /Start Metadata Import/ }));
    expect(await screen.findByRole("heading", { name: "Operation completed successfully" })).toBeTruthy();

    const submitted = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/operations/metadata-import/runs");
    expect(JSON.parse(String(submitted?.[1]?.body))).toEqual({
      job_name: "Import Products",
      upload_token: "metadata-upload-123",
      inbox_file: null,
      error_file_name: "Metadata_Errors.csv",
      refresh_job_name: "Refresh_Cube",
      planning_task_id: 47
    });
  });

  it("runs Metadata Import with an automatically selected Oracle Inbox file", async () => {
    window.location.hash = "#operations";
    const administrator = {
      ...bootstrap,
      user: { ...bootstrap.user!, platform_roles: ["SERVICE_ADMINISTRATOR"], permissions: ["operation.execute"], persona: "SERVICE_ADMINISTRATOR" as const, persona_label: "Service Administrator" },
      navigation: [...bootstrap.navigation, { code: "operations", label: "Operations", path: "#operations", group: "Automation" }]
    };
    const operation = { code: "metadata-import", display_name: "Metadata Import", description: "Import governed Planning metadata and optionally refresh the cube.", category: "Application administration", risk_level: "Elevated", route: "/app/operations/metadata-import" };
    const execution = { execution_id: "metadata-inbox-1", operation_name: "Metadata Import - Import Products", status: "SUCCESS", started_at: "2026-08-11T10:00:00Z", completed_at: "2026-08-11T10:00:08Z", error_message: null, initiated_by: "Planning Administrator", trigger_source: "MANUAL", steps: [], completed_steps: 1, total_steps: 1, artifacts: [], log_url: null, terminal: true };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(administrator);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/operations") return response({ status: "success", operations: [operation] });
      if (url === "/api/v1/operations/metadata-import/catalog") return response({ status: "success", jobs: ["Import Products"], refresh_jobs: ["Refresh_Cube"] });
      if (url === "/api/v1/operations/files/catalog?purpose=metadata-import") return response({
        status: "success",
        purpose: "metadata-import",
        files: [
          { name: "Entity_Metadata.csv", folder: "Inbox", file_type: "EXTERNAL", size_bytes: 4096, last_modified_epoch_ms: 1786420800000 }
        ]
      });
      if (url === "/api/v1/operations/metadata-import/runs" && init?.method === "POST") return response({ status: "accepted", execution_id: execution.execution_id, redirect: `/app/operations/runs/${execution.execution_id}` }, 202);
      if (url === `/api/v1/operations/runs/${execution.execution_id}`) return response(execution);
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Open service/ }));
    fireEvent.change(await screen.findByLabelText("Import Metadata job"), { target: { value: "Import Products" } });
    fireEvent.click(screen.getByRole("radio", { name: /Choose from Oracle Inbox/ }));
    const filePicker = await screen.findByLabelText("Existing Oracle metadata file");
    await waitFor(() => expect((filePicker as HTMLSelectElement).value).toBe("Entity_Metadata.csv"));
    fireEvent.click(screen.getByRole("button", { name: /Review Metadata Import/ }));

    expect(await screen.findByRole("heading", { name: "Review the Metadata Import" })).toBeTruthy();
    expect(screen.getByText("Oracle Inbox")).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/I reviewed the saved job/));
    fireEvent.click(screen.getByRole("button", { name: /Start Metadata Import/ }));
    expect(await screen.findByRole("heading", { name: "Operation completed successfully" })).toBeTruthy();

    const submitted = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/operations/metadata-import/runs");
    expect(JSON.parse(String(submitted?.[1]?.body))).toEqual({
      job_name: "Import Products",
      upload_token: null,
      inbox_file: "Entity_Metadata.csv",
      error_file_name: null,
      refresh_job_name: null
    });
    expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/v1/uploads?filename="))).toBe(false);
  });

  it("runs an exact saved Cube Refresh job from an assigned task", async () => {
    window.history.replaceState(
      {},
      "",
      "/?operation=cube-refresh&planning_task_id=55#operations"
    );
    const administrator = {
      ...bootstrap,
      user: { ...bootstrap.user!, platform_roles: ["SERVICE_ADMINISTRATOR"], permissions: ["operation.execute"], persona: "SERVICE_ADMINISTRATOR" as const, persona_label: "Service Administrator" },
      navigation: [...bootstrap.navigation, { code: "operations", label: "Operations", path: "#operations", group: "Automation" }]
    };
    const operation = { code: "cube-refresh", display_name: "Planning Cube Refresh", description: "Synchronize Planning metadata with the underlying cube.", category: "Application administration", risk_level: "Elevated", route: "/app/operations/cube-refresh" };
    const execution = { execution_id: "refresh-execution-1", operation_name: "Planning Cube Refresh - Refresh_Cube", status: "SUCCESS", started_at: "2026-08-11T09:00:00Z", completed_at: "2026-08-11T09:00:20Z", error_message: null, initiated_by: "Planning Administrator", trigger_source: "PLANNING_TASK", steps: [], completed_steps: 1, total_steps: 1, artifacts: [], log_url: null, terminal: true };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(administrator);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/operations") return response({ status: "success", operations: [operation] });
      if (url === "/api/v1/operations/cube-refresh/catalog") return response({ status: "success", jobs: [] });
      if (url === "/api/v1/operations/cube-refresh/runs" && init?.method === "POST") return response({ status: "accepted", execution_id: execution.execution_id, redirect: `/app/operations/runs/${execution.execution_id}` }, 202);
      if (url === `/api/v1/operations/runs/${execution.execution_id}`) return response(execution);
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Planning Cube Refresh" })).toBeTruthy();
    expect(await screen.findByText("Job discovery is limited")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Saved Cube Refresh job"), { target: { value: "Refresh_Cube" } });
    fireEvent.click(screen.getByRole("button", { name: /Review Cube Refresh/ }));

    expect(await screen.findByRole("heading", { name: "Review the Cube Refresh" })).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/I confirmed the exact saved job/));
    fireEvent.click(screen.getByRole("button", { name: /Start Cube Refresh/ }));
    expect(await screen.findByRole("heading", { name: "Operation completed successfully" })).toBeTruthy();

    const submitted = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/operations/cube-refresh/runs");
    expect(JSON.parse(String(submitted?.[1]?.body))).toEqual({
      job_name: "Refresh_Cube",
      planning_task_id: 55
    });
  });

  it("safely updates an existing live substitution variable", async () => {
    window.location.hash = "#operations";
    const administrator = {
      ...bootstrap,
      user: { ...bootstrap.user!, platform_roles: ["SERVICE_ADMINISTRATOR"], permissions: ["operation.execute", "variable.update"], persona: "SERVICE_ADMINISTRATOR" as const, persona_label: "Service Administrator" },
      navigation: [...bootstrap.navigation, { code: "operations", label: "Operations", path: "#operations", group: "Automation" }]
    };
    const operation = { code: "substitution-variables", display_name: "Substitution Variables", description: "Review and safely maintain Planning variables.", category: "Application administration", risk_level: "Elevated", route: "/app/operations/substitution-variables" };
    const execution = { execution_id: "variable-update-1", operation_name: "Substitution Variable - ALL.CurYr", status: "SUCCESS", started_at: "2026-08-11T10:00:00Z", completed_at: "2026-08-11T10:00:02Z", error_message: null, initiated_by: "Planning Administrator", trigger_source: "MANUAL", steps: [], completed_steps: 1, total_steps: 1, artifacts: [], log_url: null, terminal: true };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(administrator);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/operations") return response({ status: "success", operations: [operation] });
      if (url === "/api/v1/substitution-variables/catalog") return response({ status: "success", catalog: { variables: [{ name: "CurYr", value: "FY25", scope: "ALL" }, { name: "CurPeriod", value: "Jan", scope: "Plan1" }], plan_types: [{ name: "Plan1", cube_name: "Plan1", identifier: 1, cube_type: 0, dimension_count: 10 }], scopes: ["ALL", "Plan1"] } });
      if (url === "/api/v1/operations/substitution-variables/runs" && init?.method === "POST") return response({ status: "accepted", execution_id: execution.execution_id, redirect: `/app/operations/runs/${execution.execution_id}` }, 202);
      if (url === `/api/v1/operations/runs/${execution.execution_id}`) return response(execution);
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Open service/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Edit CurYr in ALL" }));
    fireEvent.change(screen.getByLabelText("New variable value"), { target: { value: "FY26" } });
    fireEvent.click(screen.getByRole("button", { name: /Review Variable Update/ }));

    expect(await screen.findByText("Protected against stale updates")).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/I reviewed the scope/));
    fireEvent.click(screen.getByRole("button", { name: /Update Variable/ }));
    expect(await screen.findByRole("heading", { name: "Operation completed successfully" })).toBeTruthy();

    const submitted = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/operations/substitution-variables/runs");
    expect(JSON.parse(String(submitted?.[1]?.body))).toEqual({ action: "UPDATE", scope: "ALL", name: "CurYr", value: "FY26", expected_current_value: "FY25" });
  });

  it("creates a new cube-scoped substitution variable after review", async () => {
    window.location.hash = "#operations";
    const administrator = {
      ...bootstrap,
      user: { ...bootstrap.user!, platform_roles: ["SERVICE_ADMINISTRATOR"], permissions: ["operation.execute", "variable.update"], persona: "SERVICE_ADMINISTRATOR" as const, persona_label: "Service Administrator" },
      navigation: [...bootstrap.navigation, { code: "operations", label: "Operations", path: "#operations", group: "Automation" }]
    };
    const operation = { code: "substitution-variables", display_name: "Substitution Variables", description: "Review and safely maintain Planning variables.", category: "Application administration", risk_level: "Elevated", route: "/app/operations/substitution-variables" };
    const execution = { execution_id: "variable-create-1", operation_name: "Substitution Variable - Plan1.ForecastEnd", status: "SUCCESS", started_at: "2026-08-11T10:05:00Z", completed_at: "2026-08-11T10:05:02Z", error_message: null, initiated_by: "Planning Administrator", trigger_source: "MANUAL", steps: [], completed_steps: 1, total_steps: 1, artifacts: [], log_url: null, terminal: true };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(administrator);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/operations") return response({ status: "success", operations: [operation] });
      if (url === "/api/v1/substitution-variables/catalog") return response({ status: "success", catalog: { variables: [{ name: "CurYr", value: "FY25", scope: "ALL" }], plan_types: [{ name: "Plan1", cube_name: "Plan1", identifier: 1, cube_type: 0, dimension_count: 10 }], scopes: ["ALL", "Plan1"] } });
      if (url === "/api/v1/operations/substitution-variables/runs" && init?.method === "POST") return response({ status: "accepted", execution_id: execution.execution_id, redirect: `/app/operations/runs/${execution.execution_id}` }, 202);
      if (url === `/api/v1/operations/runs/${execution.execution_id}`) return response(execution);
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Open service/ }));
    fireEvent.click(await screen.findByRole("tab", { name: /Create new/ }));
    fireEvent.change(screen.getByLabelText("Variable scope"), { target: { value: "Plan1" } });
    fireEvent.change(screen.getByLabelText("Variable name"), { target: { value: "ForecastEnd" } });
    fireEvent.change(screen.getByLabelText("Initial variable value"), { target: { value: "Mar" } });
    fireEvent.click(screen.getByRole("button", { name: /Review New Variable/ }));

    expect(await screen.findByText("This creates a new Oracle definition")).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/I reviewed the scope/));
    fireEvent.click(screen.getByRole("button", { name: /Create Variable/ }));
    expect(await screen.findByRole("heading", { name: "Operation completed successfully" })).toBeTruthy();

    const submitted = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/operations/substitution-variables/runs");
    expect(JSON.parse(String(submitted?.[1]?.body))).toEqual({ action: "CREATE", scope: "Plan1", name: "ForecastEnd", value: "Mar", expected_current_value: null });
  });

  it("builds a Smart View-style slice and displays live Planning data", async () => {
    window.location.hash = "#data-review";
    const planner = {
      ...bootstrap,
      user: { ...bootstrap.user!, permissions: ["data.review", "report.generate"] },
      navigation: [...bootstrap.navigation, { code: "data-review", label: "Data Explorer", path: "/app/data-review", group: "Analysis" }]
    };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(planner);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/data-explorer/views" && (!init?.method || init.method === "GET")) return response({ status: "success", views: [] });
      if (url === "/api/v1/data-explorer/views" && init?.method === "POST") {
        const payload = JSON.parse(String(init.body));
        return response({ status: "success", message: `Saved view '${payload.name}' was created.`, view: { name: payload.name, title: payload.title, cube: payload.cube, default_pov: Object.entries(payload.pov), rows: payload.rows.map((item: { dimension: string; members: string[] }) => [item.dimension, item.members]), columns: payload.columns.map((item: { dimension: string; members: string[] }) => [item.dimension, item.members]) } }, 201);
      }
      if (url === "/api/v1/data-review/cubes") return response({ status: "success", cubes: [{ name: "Plan1", cube_name: "Plan1", cube_type: 0, dimension_count: 4 }, { name: "Rpt", cube_name: "Rpt", cube_type: 1, dimension_count: 4 }] });
      if (url === "/api/v1/data-review/cubes/Plan1/dimensions") return response({ status: "success", cube: "Plan1", dimensions: [{ name: "Account", dimension_type: "Account" }, { name: "Period", dimension_type: "Period" }, { name: "Scenario", dimension_type: "Scenario" }, { name: "Year", dimension_type: "Year" }] });
      if (url.startsWith("/api/v1/data-review/cubes/Plan1/dimensions/") && url.includes("/members?")) {
        const dimension = decodeURIComponent(url.split("/dimensions/")[1].split("/members?")[0]);
        const options: Record<string, string[]> = { Scenario: ["Forecast", "Actual"], Year: ["FY27"], Period: ["Jan", "Feb"], Account: ["Revenue", "Gross Profit"] };
        const members = (options[dimension] ?? []).map((name) => ({ name, alias: null, path: `/${dimension}/${name}`, parent_name: dimension, has_children: false }));
        return response({ status: "success", cube: "Plan1", dimension, query: "", members, total_matches: members.length, has_more: false });
      }
      if (url === "/api/v1/data-review/grid" && init?.method === "POST") return response({ status: "success", review: { cube: "Plan1", form_name: "Plan1 data slice", row_count: 2, column_count: 2, cell_count: 4, missing_cell_count: 1, grid: { row_dimensions: ["Account"], column_dimensions: ["Period"], columns: [["Jan"], ["Feb"]], rows: [{ headers: ["Revenue"], data: [1000, 1100] }, { headers: ["Gross Profit"], data: [400, "#Missing"] }], pov: [["Scenario", "Forecast"], ["Year", "FY27"]] } } });
      if (url === "/api/v1/data-review/grid/export/csv" && init?.method === "POST") return Promise.resolve(new Response("Scenario,Account,Period,Value\nForecast,Revenue,Jan,1000\n", { status: 200, headers: { "Content-Type": "text/csv" } }));
      if (url === "/api/v1/data-review/validate" && init?.method === "POST") return response({ status: "success", validation: { cube: "Plan1", form_name: "Plan1 validation slice", result: { status: "FAIL", checked_cells: 4, passed_cells: 3, issue_count: 1, missing_count: 1, zero_count: 0, below_minimum_count: 0, above_maximum_count: 0, non_numeric_count: 0, truncated: false, rules: { check_missing: true, check_zero: false, minimum: null, maximum: null, max_issues: 500 }, issues: [{ code: "MISSING", severity: "ERROR", message: "The selected Planning intersection has no data.", pov: [["Scenario", "Forecast"], ["Year", "FY27"]], row_headers: ["Gross Profit"], column_headers: ["Feb"], raw_value: "#Missing", numeric_value: null }] } } });
      if (url === "/api/v1/data-review/compare" && init?.method === "POST") return response({ status: "success", comparison: { source_cube: "Plan1", target_cube: "Rpt", result: { source_form: "Plan1 source slice", target_form: "Rpt target slice", compared_cells: 4, matched_cells: 3, tolerance: 0, mismatches: [], cells: [{ row_headers: ["Gross Profit"], column_headers: ["Feb"], source_value: 400, target_value: 390, difference: 10, matches: false }] } } });
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    const cubeInput = await screen.findByLabelText("Cube or plan type");
    fireEvent.change(cubeInput, { target: { value: "Plan1" } });
    const scenarioMember = await screen.findByLabelText("POV member for Scenario");
    fireEvent.focus(scenarioMember);
    fireEvent.click(await screen.findByRole("option", { name: /Forecast/ }));
    expect((screen.getByLabelText("POV member for Scenario") as HTMLInputElement).value).toBe("Forecast");
    fireEvent.focus(screen.getByLabelText("POV member for Year"));
    fireEvent.click(await screen.findByRole("option", { name: /FY27/ }));
    const columnMembers = screen.getByLabelText("Column members for Period");
    fireEvent.focus(columnMembers);
    fireEvent.click(await screen.findByRole("option", { name: /^Jan/ }));
    fireEvent.click(await screen.findByRole("option", { name: /^Feb/ }));
    const rowMembers = screen.getByLabelText("Row members for Account");
    fireEvent.focus(rowMembers);
    fireEvent.click(await screen.findByRole("option", { name: /^Revenue/ }));
    fireEvent.click(await screen.findByRole("option", { name: /^Gross Profit/ }));
    fireEvent.click(screen.getByRole("button", { name: /Load live data/ }));

    expect(await screen.findByRole("heading", { name: "Plan1 planning grid" })).toBeTruthy();
    expect(screen.getByText("Gross Profit")).toBeTruthy();
    expect(screen.getByText("1,100")).toBeTruthy();
    fireEvent.click(screen.getByText("1,000"));
    expect(screen.getByText("Revenue × Jan")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^CSV$/ }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/v1/data-review/grid/export/csv")).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: /Save view/ }));
    fireEvent.change(screen.getByLabelText("Saved view name"), { target: { value: "monthly-revenue" } });
    fireEvent.change(screen.getByLabelText("Saved view title"), { target: { value: "Monthly Revenue" } });
    fireEvent.click(screen.getAllByRole("button", { name: /^Save view/ }).at(-1)!);
    expect(await screen.findByText("Saved view 'monthly-revenue' was created.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Saved data view"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Saved data view"), { target: { value: "monthly-revenue" } });
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => String(url) === "/api/v1/data-review/grid").length).toBe(2));
    expect(await screen.findByText("Loaded 'Monthly Revenue' with current Oracle values.")).toBeTruthy();

    expect(screen.getByRole("heading", { name: "Validate this Planning grid" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Run quality checks/ }));
    expect(await screen.findByRole("heading", { name: "Data-quality exceptions found" })).toBeTruthy();
    fireEvent.click(screen.getByText("Missing value"));
    expect(screen.getByText("Scenario: Forecast · Year: FY27")).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: /Compare target cube/ }));
    fireEvent.change(screen.getByPlaceholderText("Select reporting cube"), { target: { value: "Rpt" } });
    fireEvent.click(screen.getByRole("button", { name: /Compare live data/ }));
    expect(await screen.findByRole("heading", { name: "Source-to-target differences found" })).toBeTruthy();

    const submitted = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/data-review/grid");
    expect(JSON.parse(String(submitted?.[1]?.body))).toEqual({ cube: "Plan1", pov: { Scenario: "Forecast", Year: "FY27" }, rows: [{ dimension: "Account", members: ["Revenue", "Gross Profit"] }], columns: [{ dimension: "Period", members: ["Jan", "Feb"] }] });
    expect(new Headers(submitted?.[1]?.headers).get("X-CSRF-Token")).toBe("test-csrf");
    const validated = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/data-review/validate");
    expect(new Headers(validated?.[1]?.headers).get("X-CSRF-Token")).toBe("test-csrf");
    const compared = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/data-review/compare");
    expect(JSON.parse(String(compared?.[1]?.body)).target.cube).toBe("Rpt");
    fireEvent.click(screen.getByRole("button", { name: /Start new view/ }));
    expect((screen.getByLabelText("Cube or plan type") as HTMLSelectElement).value).toBe("");
    expect(screen.queryByRole("heading", { name: "Plan1 planning grid" })).toBeNull();
  });

  it("provides a governed EPM Assistant conversation and action handoff", async () => {
    window.location.hash = "#assistant";
    const assistantUser = {
      ...bootstrap,
      user: { ...bootstrap.user!, permissions: ["agent.use", "operation.execute"] },
      navigation: [...bootstrap.navigation, { code: "assistant", label: "EPM Assistant", path: "/app/agent", group: "Workspace" }]
    };
    const conversation = { conversation_id: "conv-1", user_id: 7, title: "Available operations", provider: "gemini", model: "gemini-test", created_at: "2026-08-11T10:00:00Z", updated_at: "2026-08-11T10:00:00Z" };
    const assistantMessage = { message_id: 2, conversation_id: "conv-1", role: "assistant", content: "I can prepare **Business Rules** for governed review.", created_at: "2026-08-11T10:01:00Z" };
    const approvalMessage = { message_id: 3, conversation_id: "conv-1", role: "assistant", content: "Review the selected rule preparation.", created_at: "2026-08-11T10:01:30Z" };
    const completedMessage = { message_id: 4, conversation_id: "conv-1", role: "assistant", content: "The governed preparation is ready.", created_at: "2026-08-11T10:02:00Z" };
    const clarification = { request_id: "clarification-1", operation_code: "business-rules", display_name: "Business Rules", prompt: "Choose the Business Rules artifact to prepare.", options: ["Revenue Forecast", "Gross Margin", "Rule 03", "Rule 04", "Rule 05", "Rule 06", "Rule 07", "Rule 08", "Rule 09", "Rule 10", "Rule 11", "Rule 12", "Rule 13"], allows_cancel: true, recommendations: [{ name: "Revenue Forecast", confidence: "Strong match", score: 92, reason: "Matches task terms: revenue, forecast." }, { name: "Stale Rule", confidence: "Possible match", score: 20, reason: "No longer available." }] };
    const inputRequest = { request_id: "input-1", operation_code: "business-rules", display_name: "Business Rules", artifact_name: "Revenue Forecast", title: "Choose runtime prompt values", description: "Use Calculation Manager defaults or provide exact prompt names and values.", fields: [{ key: "runtime_prompt_mode", label: "Runtime prompt source", kind: "choice", required: true, description: "Use configured defaults or explicitly override them.", placeholder: "", options: ["Use Calculation Manager defaults", "Provide runtime prompt values"] }, { key: "runtime_prompts", label: "Runtime prompts", kind: "key_value", required: false, description: "Exact Calculation Manager RTP names and values.", placeholder: "Name=Value, one per line", options: [] }] };
    const approval = { request_id: "approval-1", operation_code: "business-rules", display_name: "Business Rules", objective: "Calculate the approved forecast.", artifact_name: "Revenue Forecast", category: "Calculation", risk_level: "Controlled", route: "/app/operations/business-rules", effect: "Start the selected Business Rule in Oracle and monitor its execution.", input_values: { runtime_prompts: {} } };
    let created = false;
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(assistantUser);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/agent/status") return response({ enabled: true, configured: true, provider: "gemini", model: "gemini-test", mode: "read-only", message: "Agent is ready." });
      if (url === "/api/v1/agent/conversations" && init?.method === "POST") { created = true; return response({ status: "success", conversation }); }
      if (url === "/api/v1/agent/conversations") return response({ status: "success", conversations: created ? [conversation] : [] });
      if (url === "/api/v1/agent/conversations/conv-1/messages" && init?.method === "POST") return response({ status: "success", message: assistantMessage, tool_activity: [], action_drafts: [], approval_request: null, clarification_request: clarification, input_request: null });
      if (url === "/api/v1/agent/conversations/conv-1/clarification" && init?.method === "POST") return response({ status: "success", message: approvalMessage, tool_activity: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: inputRequest });
      if (url === "/api/v1/agent/conversations/conv-1/inputs" && init?.method === "POST") return response({ status: "success", message: approvalMessage, tool_activity: [], action_drafts: [], approval_request: approval, clarification_request: null, input_request: null });
      if (url === "/api/v1/agent/conversations/conv-1/approval" && init?.method === "POST") return response({ status: "success", message: completedMessage, tool_activity: [{ name: "prepare_operation_action", arguments: { operation_code: "business-rules" }, status: "SUCCESS", summary: "Rule approved and queued." }], action_drafts: [], approval_request: null, clarification_request: null, input_request: null, execution: { execution_id: "rule-execution-1", operation_code: "business-rules", target_name: "Revenue Forecast", status: "QUEUED" } });
      if (url === "/api/v1/operations/runs/rule-execution-1") return response({ execution_id: "rule-execution-1", operation_name: "Business Rule - Revenue Forecast", status: "SUCCESS", started_at: "2026-08-11T10:02:00Z", completed_at: "2026-08-11T10:02:05Z", error_message: null, initiated_by: "Finance Planner", trigger_source: "AI_AGENT", steps: [], completed_steps: 3, total_steps: 3, artifacts: [], log_url: null, terminal: true });
      if (url === "/api/v1/agent/conversations/conv-1/messages") return response({ status: "success", messages: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null });
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "How can I help with Planning?" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Prepare a governed draft to run a business rule/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Send/ }));

    expect(await screen.findByRole("heading", { name: "Matches found for your task" })).toBeTruthy();
    expect(screen.queryByText("Stale Rule")).toBeNull();
    fireEvent.change(screen.getByPlaceholderText("Search current Business Rules"), { target: { value: "Gross" } });
    fireEvent.click(screen.getByRole("button", { name: /Revenue Forecast.*Strong match/ }));
    expect((screen.getByLabelText("Business Rules artifact") as HTMLSelectElement).value).toBe("Revenue Forecast");
    fireEvent.click(screen.getByRole("button", { name: /^Continue/ }));
    const promptMode = await screen.findByLabelText(/Runtime prompt source/);
    fireEvent.change(promptMode, { target: { value: "Use Calculation Manager defaults" } });
    fireEvent.click(screen.getByRole("button", { name: /Continue to approval/ }));
    expect(await screen.findByRole("heading", { name: "Run Business Rules?" })).toBeTruthy();
    expect(screen.getByText(/This approval starts the Oracle operation/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Approve and run/ }));
    expect(await screen.findByText("Verified with platform tools")).toBeTruthy();
    expect(await screen.findByRole("heading", { name: "Revenue Forecast" })).toBeTruthy();
    expect(await screen.findByText("Business Rule completed successfully.")).toBeTruthy();
    expect(screen.getByLabelText("Approved Business Rule execution")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Dismiss execution status" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Message the EPM Assistant"), { target: { value: "What should I do next?" } });
    fireEvent.click(screen.getByRole("button", { name: /^Send/ }));
    await waitFor(() => expect(screen.queryByLabelText("Approved Business Rule execution")).toBeNull());
    expect(screen.queryByText("Governed action draft")).toBeNull();
    const send = fetchMock.mock.calls.find(([url, init]) => String(url) === "/api/v1/agent/conversations/conv-1/messages" && init?.method === "POST");
    expect(new Headers(send?.[1]?.headers).get("X-CSRF-Token")).toBe("test-csrf");
    const approved = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/agent/conversations/conv-1/approval");
    expect(JSON.parse(String(approved?.[1]?.body))).toEqual({ request_id: "approval-1", decision: "approve" });
    const clarified = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/agent/conversations/conv-1/clarification");
    expect(JSON.parse(String(clarified?.[1]?.body))).toEqual({ request_id: "clarification-1", value: "Revenue Forecast" });
    const inputs = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/agent/conversations/conv-1/inputs");
    expect(JSON.parse(String(inputs?.[1]?.body))).toEqual({ request_id: "input-1", values: { runtime_prompt_mode: "Use Calculation Manager defaults", runtime_prompts: {} } });
  });

  it("recovers a missing Pipeline through synchronization and exact registration", async () => {
    window.location.hash = "#assistant";
    const assistantUser = {
      ...bootstrap,
      user: { ...bootstrap.user!, permissions: ["agent.use", "operation.execute", "catalog.manage"] },
      navigation: [...bootstrap.navigation, { code: "assistant", label: "EPM Assistant", path: "/app/agent", group: "Workspace" }]
    };
    const conversation = { conversation_id: "conv-recovery", user_id: 7, title: "Find monthly Pipeline", provider: "gemini", model: "gemini-test", created_at: "2026-08-11T10:00:00Z", updated_at: "2026-08-11T10:00:00Z" };
    const message = (id: number, content: string) => ({ message_id: id, conversation_id: "conv-recovery", role: "assistant", content, created_at: "2026-08-11T10:01:00Z" });
    const recovery = { enabled: true, can_manage: true, identifier_label: "Exact Oracle Pipeline code", identifier_placeholder: "For example: PIPE01", registration_mode: "verified", help: "The exact code is checked with a read-only Oracle request before registration." };
    const clarification = { request_id: "choice-recovery", operation_code: "pipelines", display_name: "Pipelines", prompt: "Choose a Pipeline.", options: [], option_labels: {}, allows_cancel: true, recommendations: [], catalog_recovery: recovery, search_context: "monthly revenue" };
    const synchronizedClarification = { ...clarification, options: ["PIPE_EXISTING"], option_labels: { PIPE_EXISTING: "Monthly Revenue Pipeline" }, recommendations: [{ name: "PIPE_EXISTING", display_name: "Monthly Revenue Pipeline", confidence: "Strong match", score: 90, reason: "Matches task terms: monthly, revenue." }] };
    const inputRequest = { request_id: "input-recovery", operation_code: "pipelines", display_name: "Pipelines", artifact_name: "PIPE_NEW", title: "Review the live Pipeline inputs", description: "These stages and inputs were read from Oracle.", fields: [{ key: "pipeline_review", label: "Live Oracle Pipeline", kind: "pipeline_review", required: true, description: "Review live inputs.", placeholder: "", options: [] }], context: { code: "PIPE_NEW", display_name: "New Monthly Pipeline", variables: [], file_requirements: [], stages: [] } };
    let created = false;
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(assistantUser);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/agent/status") return response({ enabled: true, configured: true, provider: "gemini", model: "gemini-test", mode: "governed", message: "Agent is ready." });
      if (url === "/api/v1/agent/conversations" && init?.method === "POST") { created = true; return response({ status: "success", conversation }); }
      if (url === "/api/v1/agent/conversations") return response({ status: "success", conversations: created ? [conversation] : [] });
      if (url === "/api/v1/agent/conversations/conv-recovery/messages" && init?.method === "POST") return response({ status: "success", message: message(2, "No registered match was found."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: clarification, input_request: null });
      if (url === "/api/v1/agent/conversations/conv-recovery/artifacts/synchronize" && init?.method === "POST") return response({ status: "success", message: "Catalog synchronized.", clarification_request: synchronizedClarification });
      if (url === "/api/v1/agent/conversations/conv-recovery/artifacts/register" && init?.method === "POST") return response({ status: "success", message: message(3, "Review the Pipeline inputs."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: inputRequest });
      if (url === "/api/v1/agent/conversations/conv-recovery/messages") return response({ status: "success", messages: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null });
      return response({ detail: `Unexpected request: ${url}` }, 404);
    });
    render(<App />);

    fireEvent.change(await screen.findByLabelText("Message the EPM Assistant"), { target: { value: "Run a monthly revenue Pipeline." } });
    fireEvent.click(screen.getByRole("button", { name: /^Send/ }));
    expect(await screen.findByRole("heading", { name: "No registered match was found" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Synchronize with Oracle/ }));
    expect(await screen.findByRole("heading", { name: "Matches found for your task" })).toBeTruthy();
    expect(screen.getByText("Monthly Revenue Pipeline")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Register an exact identifier/ }));
    fireEvent.change(screen.getByLabelText("Exact Oracle Pipeline code"), { target: { value: "PIPE_NEW" } });
    fireEvent.click(screen.getByRole("button", { name: /Verify and continue/ }));
    expect(await screen.findByRole("heading", { name: "Review the live Pipeline inputs" })).toBeTruthy();

    const synchronized = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/artifacts/synchronize"));
    expect(JSON.parse(String(synchronized?.[1]?.body))).toEqual({ request_id: "choice-recovery" });
    const registered = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/artifacts/register"));
    expect(JSON.parse(String(registered?.[1]?.body))).toEqual({ request_id: "choice-recovery", identifier: "PIPE_NEW" });
  });

  it("runs an approved Data Map directly from the EPM Assistant", async () => {
    window.location.hash = "#assistant";
    const assistantUser = {
      ...bootstrap,
      user: { ...bootstrap.user!, permissions: ["agent.use", "operation.execute"] },
      navigation: [...bootstrap.navigation, { code: "assistant", label: "EPM Assistant", path: "/app/agent", group: "Workspace" }]
    };
    const conversation = { conversation_id: "conv-map", user_id: 7, title: "Publish revenue", provider: "gemini", model: "gemini-test", created_at: "2026-08-11T10:00:00Z", updated_at: "2026-08-11T10:00:00Z" };
    const message = (id: number, content: string) => ({ message_id: id, conversation_id: "conv-map", role: "assistant", content, created_at: "2026-08-11T10:01:00Z" });
    const clarification = { request_id: "clarification-map", operation_code: "data-maps", display_name: "Data Maps", prompt: "Choose a Data Map.", options: ["Revenue to Reporting"], allows_cancel: true, recommendations: [] };
    const inputRequest = { request_id: "input-map", operation_code: "data-maps", display_name: "Data Maps", artifact_name: "Revenue to Reporting", title: "Choose how the Data Map should run", description: "Use the configured map or optionally override its source region.", fields: [{ key: "clear_target", label: "Clear target", kind: "boolean", required: true, description: "Clear mapped target first.", placeholder: "", options: [] }] };
    const approval = { request_id: "approval-map", operation_code: "data-maps", display_name: "Data Maps", objective: "Publish approved revenue.", artifact_name: "Revenue to Reporting", category: "Data movement", risk_level: "Elevated", route: "/app/operations/data-maps", effect: "Start the selected Data Map in Oracle with the reviewed clear-target and override choices, then monitor its execution.", input_values: { clear_target: false, member_overrides: {}, exclusion_overrides: {} } };
    let created = false;
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(assistantUser);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/agent/status") return response({ enabled: true, configured: true, provider: "gemini", model: "gemini-test", mode: "governed", message: "Agent is ready." });
      if (url === "/api/v1/agent/conversations" && init?.method === "POST") { created = true; return response({ status: "success", conversation }); }
      if (url === "/api/v1/agent/conversations") return response({ status: "success", conversations: created ? [conversation] : [] });
      if (url === "/api/v1/agent/conversations/conv-map/messages" && init?.method === "POST") return response({ status: "success", message: message(2, "Choose a live Data Map."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: clarification, input_request: null });
      if (url === "/api/v1/agent/conversations/conv-map/clarification" && init?.method === "POST") return response({ status: "success", message: message(3, "Choose the run controls."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: inputRequest });
      if (url === "/api/v1/agent/conversations/conv-map/inputs" && init?.method === "POST") return response({ status: "success", message: message(4, "Review and approve."), tool_activity: [], action_drafts: [], approval_request: approval, clarification_request: null, input_request: null });
      if (url === "/api/v1/agent/conversations/conv-map/approval" && init?.method === "POST") return response({ status: "success", message: message(5, "Data Map queued."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null, execution: { execution_id: "map-execution-1", operation_code: "data-maps", target_name: "Revenue to Reporting", status: "QUEUED" } });
      if (url === "/api/v1/operations/runs/map-execution-1") return response({ execution_id: "map-execution-1", operation_name: "Data Map - Revenue to Reporting", status: "SUCCESS", started_at: "2026-08-11T10:02:00Z", completed_at: "2026-08-11T10:02:05Z", error_message: null, initiated_by: "Finance Planner", trigger_source: "AI_AGENT", steps: [], completed_steps: 3, total_steps: 3, artifacts: [], log_url: null, terminal: true });
      if (url === "/api/v1/agent/conversations/conv-map/messages") return response({ status: "success", messages: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null });
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "How can I help with Planning?" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Message the EPM Assistant"), { target: { value: "Publish approved revenue to reporting." } });
    fireEvent.click(screen.getByRole("button", { name: /^Send/ }));
    fireEvent.change(await screen.findByLabelText("Data Maps artifact"), { target: { value: "Revenue to Reporting" } });
    fireEvent.click(screen.getByRole("button", { name: /^Continue/ }));
    fireEvent.change(await screen.findByLabelText(/Clear target before push/), { target: { value: "false" } });
    fireEvent.click(screen.getByRole("button", { name: /Continue to approval/ }));
    expect(await screen.findByRole("heading", { name: "Run Data Maps?" })).toBeTruthy();
    expect(screen.getByText("No")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Approve and run/ }));
    expect(await screen.findByText("Data Map completed successfully.")).toBeTruthy();
    expect(screen.queryByText("Governed action draft")).toBeNull();
    const submitted = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/agent/conversations/conv-map/inputs");
    expect(JSON.parse(String(submitted?.[1]?.body))).toEqual({ request_id: "input-map", values: { clear_target: false, member_overrides: {}, exclusion_overrides: {} } });
  });

  it("runs a live-preflighted Oracle Pipeline directly from the EPM Assistant", async () => {
    window.location.hash = "#assistant";
    const assistantUser = {
      ...bootstrap,
      user: { ...bootstrap.user!, permissions: ["agent.use", "operation.execute"] },
      navigation: [...bootstrap.navigation, { code: "assistant", label: "EPM Assistant", path: "/app/agent", group: "Workspace" }]
    };
    const conversation = { conversation_id: "conv-pipe", user_id: 7, title: "Run forecast Pipeline", provider: "gemini", model: "gemini-test", created_at: "2026-08-11T10:00:00Z", updated_at: "2026-08-11T10:00:00Z" };
    const message = (id: number, content: string) => ({ message_id: id, conversation_id: "conv-pipe", role: "assistant", content, created_at: "2026-08-11T10:01:00Z" });
    const clarification = { request_id: "choice-pipe", operation_code: "pipelines", display_name: "Pipelines", prompt: "Choose a Pipeline.", options: ["PIPE01"], option_labels: { PIPE01: "Monthly Revenue Forecast" }, allows_cancel: true, recommendations: [{ name: "PIPE01", display_name: "Monthly Revenue Forecast", confidence: "Strong match", score: 92, reason: "Matches task terms: revenue, forecast." }] };
    const inputRequest = {
      request_id: "input-pipe",
      operation_code: "pipelines",
      display_name: "Pipelines",
      artifact_name: "PIPE01",
      title: "Review the live Pipeline inputs",
      description: "These stages and inputs were read from Oracle.",
      fields: [{ key: "pipeline_review", label: "Live Oracle Pipeline", kind: "pipeline_review", required: true, description: "Review live inputs.", placeholder: "", options: [] }],
      context: {
        code: "PIPE01",
        display_name: "Monthly Forecast",
        variables: [
          { name: "YEAR", display_name: "Planning Year", default_value: "FY27", required: true, editable: true },
          { name: "STARTPERIOD", display_name: "Start Period", default_value: null, required: true, editable: true }
        ],
        file_requirements: [{ key: "DataLoad_File", display_name: "Forecast data", configured_reference: "OldForecast.csv", required: true, allowed_extensions: [".csv"], consumers: ["Load forecast"] }],
        stages: [{ name: "LOAD", display_name: "Load forecast", job_count: 1, runs_in_parallel: false }]
      }
    };
    const approval = { request_id: "approval-pipe", operation_code: "pipelines", display_name: "Pipelines", objective: "Run monthly forecast.", artifact_name: "PIPE01", category: "Orchestration", risk_level: "Elevated", route: "/app/operations/pipelines", effect: "Start the selected Oracle Pipeline with the reviewed live variables and file choices, then monitor the complete run.", input_values: { runtime_variables: { YEAR: "FY27", STARTPERIOD: "Jan-27" }, uploads: { DataLoad_File: "upload-1" }, upload_names: { DataLoad_File: "Forecast.csv" }, inbox_files: {}, configured_files: {} } };
    let created = false;
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(assistantUser);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/agent/status") return response({ enabled: true, configured: true, provider: "gemini", model: "gemini-test", mode: "governed", message: "Agent is ready." });
      if (url === "/api/v1/agent/conversations" && init?.method === "POST") { created = true; return response({ status: "success", conversation }); }
      if (url === "/api/v1/agent/conversations") return response({ status: "success", conversations: created ? [conversation] : [] });
      if (url === "/api/v1/agent/conversations/conv-pipe/messages" && init?.method === "POST") return response({ status: "success", message: message(2, "Choose a matching Pipeline."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: clarification, input_request: null });
      if (url === "/api/v1/agent/conversations/conv-pipe/clarification" && init?.method === "POST") return response({ status: "success", message: message(3, "Review the Pipeline inputs."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: inputRequest });
      if (url.startsWith("/api/v1/uploads?filename=") && init?.method === "POST") return response({ status: "success", upload: { token: "upload-1", filename: "Forecast.csv", size: 24 } });
      if (url === "/api/v1/agent/conversations/conv-pipe/inputs" && init?.method === "POST") return response({ status: "success", message: message(3, "Review and approve."), tool_activity: [], action_drafts: [], approval_request: approval, clarification_request: null, input_request: null });
      if (url === "/api/v1/agent/conversations/conv-pipe/approval" && init?.method === "POST") return response({ status: "success", message: message(4, "Pipeline queued."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null, execution: { execution_id: "pipe-execution-1", operation_code: "pipelines", target_name: "PIPE01", status: "QUEUED" } });
      if (url === "/api/v1/operations/runs/pipe-execution-1") return response({ execution_id: "pipe-execution-1", operation_name: "Pipeline - PIPE01", status: "SUCCESS", started_at: "2026-08-11T10:02:00Z", completed_at: "2026-08-11T10:02:05Z", error_message: null, initiated_by: "Finance Planner", trigger_source: "AI_AGENT", steps: [], completed_steps: 3, total_steps: 3, artifacts: [], log_url: null, terminal: true });
      if (url === "/api/v1/agent/conversations/conv-pipe/messages") return response({ status: "success", messages: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null });
      return response({ detail: `Unexpected request: ${url}` }, 404);
    });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "How can I help with Planning?" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Message the EPM Assistant"), { target: { value: "Run Pipeline PIPE01." } });
    fireEvent.click(screen.getByRole("button", { name: /^Send/ }));
    expect(await screen.findByRole("heading", { name: "Matches found for your task" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Monthly Revenue Forecast.*Strong match/ }));
    const continueButton = screen.getByRole("button", { name: /^Continue/ });
    await waitFor(() => expect((continueButton as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(continueButton);
    expect(await screen.findByText("Load forecast")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Pipeline variable Start Period"), { target: { value: "Jan-27" } });
    fireEvent.change(screen.getByLabelText("Forecast data file source"), { target: { value: "upload" } });
    fireEvent.change(screen.getByLabelText("Forecast data local file"), { target: { files: [new File(["Account,Jan\nRevenue,100"], "Forecast.csv", { type: "text/csv" })] } });
    fireEvent.click(screen.getByRole("button", { name: /Continue to approval/ }));
    expect(await screen.findByRole("heading", { name: "Run Pipelines?" }, { timeout: 15000 })).toBeTruthy();
    expect(screen.getByText("Forecast.csv")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Approve and run/ }));
    expect(await screen.findByText("Pipeline completed successfully.")).toBeTruthy();
    expect(screen.queryByText("Governed action draft")).toBeNull();
    const inputs = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/agent/conversations/conv-pipe/inputs");
    expect(JSON.parse(String(inputs?.[1]?.body))).toEqual({ request_id: "input-pipe", values: { runtime_variables: { YEAR: "FY27", STARTPERIOD: "Jan-27" }, file_choices: { DataLoad_File: { source: "upload", upload_token: "upload-1", filename: "Forecast.csv" } } } });
  });

  it("runs a Data Integration directly from one guided assistant approval", async () => {
    window.location.hash = "#assistant";
    const assistantUser = {
      ...bootstrap,
      user: { ...bootstrap.user!, permissions: ["agent.use", "operation.execute"] },
      navigation: [...bootstrap.navigation, { code: "assistant", label: "EPM Assistant", path: "/app/agent", group: "Workspace" }]
    };
    const conversation = { conversation_id: "conv-integration", user_id: 7, title: "Load revenue data", provider: "gemini", model: "gemini-test", created_at: "2026-08-11T10:00:00Z", updated_at: "2026-08-11T10:00:00Z" };
    const message = (id: number, content: string) => ({ message_id: id, conversation_id: "conv-integration", role: "assistant", content, created_at: "2026-08-11T10:01:00Z" });
    const inputRequest = {
      request_id: "input-integration",
      operation_code: "data-integrations",
      display_name: "Data Integrations",
      artifact_name: "Revenue_Load",
      title: "Choose the Data Integration run inputs",
      description: "Select periods, modes, and one source file.",
      fields: [{ key: "start_period", label: "Start period", kind: "text", required: true, description: "Start", placeholder: "Jan-27", options: [] }],
      context: { import_modes: ["Replace", "Append"], export_modes: ["Merge", "Replace"], allowed_extensions: [".csv", ".txt", ".zip", ".dat"], prefill: { year: "FY27", start_month: "Jan", end_month: "Mar" }, task_context: { scenario: "Actual", period: "Jan", year: "FY27" } }
    };
    const approval = { request_id: "approval-integration", operation_code: "data-integrations", display_name: "Data Integrations", objective: "Load monthly revenue data.", artifact_name: "Revenue_Load", category: "Data loading", risk_level: "Elevated", route: "/app/operations/data-integrations", effect: "Start the selected Data Integration with the reviewed period range, modes, and source file, then monitor the complete load.", input_values: { start_period: "Jan-27", end_period: "Mar-27", import_mode: "Replace", export_mode: "Merge", file_source: "Upload on governed screen", inbox_file: "", upload_token: "integration-upload-1", upload_name: "Revenue.csv" } };
    let created = false;
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(assistantUser);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/agent/status") return response({ enabled: true, configured: true, provider: "gemini", model: "gemini-test", mode: "governed", message: "Agent is ready." });
      if (url === "/api/v1/agent/conversations" && init?.method === "POST") { created = true; return response({ status: "success", conversation }); }
      if (url === "/api/v1/agent/conversations") return response({ status: "success", conversations: created ? [conversation] : [] });
      if (url === "/api/v1/agent/conversations/conv-integration/messages" && init?.method === "POST") return response({ status: "success", message: message(2, "Choose the Data Integration inputs."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: inputRequest });
      if (url.startsWith("/api/v1/uploads?filename=") && init?.method === "POST") return response({ status: "success", upload: { token: "integration-upload-1", filename: "Revenue.csv", size: 24 } });
      if (url === "/api/v1/agent/conversations/conv-integration/inputs" && init?.method === "POST") return response({ status: "success", message: message(3, "Review and approve."), tool_activity: [], action_drafts: [], approval_request: approval, clarification_request: null, input_request: null });
      if (url === "/api/v1/agent/conversations/conv-integration/approval" && init?.method === "POST") return response({ status: "success", message: message(4, "Data Integration queued."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null, execution: { execution_id: "integration-execution-1", operation_code: "data-integrations", target_name: "Revenue_Load", status: "QUEUED" } });
      if (url === "/api/v1/operations/runs/integration-execution-1") return response({ execution_id: "integration-execution-1", operation_name: "Data Integration - Revenue_Load", status: "SUCCESS", started_at: "2026-08-11T10:02:00Z", completed_at: "2026-08-11T10:02:05Z", error_message: null, initiated_by: "Finance Planner", trigger_source: "AI_AGENT", steps: [], completed_steps: 3, total_steps: 3, artifacts: [], log_url: null, terminal: true });
      if (url === "/api/v1/agent/conversations/conv-integration/messages") return response({ status: "success", messages: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null });
      return response({ detail: `Unexpected request: ${url}` }, 404);
    });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "How can I help with Planning?" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Message the EPM Assistant"), { target: { value: "Run Revenue Load Data Integration." } });
    fireEvent.click(screen.getByRole("button", { name: /^Send/ }));
    expect(await screen.findByText("Revenue_Load")).toBeTruthy();
    expect(screen.getByText("Actual · Jan · FY27")).toBeTruthy();
    expect((screen.getByLabelText("Agent Data Integration planning year") as HTMLSelectElement).value).toBe("FY27");
    expect((screen.getByLabelText("Agent Data Integration start month") as HTMLSelectElement).value).toBe("Jan");
    expect((screen.getByLabelText("Agent Data Integration end month") as HTMLSelectElement).value).toBe("Mar");
    fireEvent.change(screen.getByLabelText("Agent Data Integration planning year"), { target: { value: "FY27" } });
    fireEvent.change(screen.getByLabelText("Agent Data Integration start month"), { target: { value: "Jan" } });
    fireEvent.change(screen.getByLabelText("Agent Data Integration end month"), { target: { value: "Mar" } });
    fireEvent.click(screen.getByRole("radio", { name: /Upload local file/ }));
    fireEvent.change(screen.getByLabelText("Agent Data Integration local file"), { target: { files: [new File(["Account,Jan\nRevenue,100"], "Revenue.csv", { type: "text/csv" })] } });
    fireEvent.click(screen.getByRole("button", { name: /Continue to approval/ }));
    expect(await screen.findByRole("heading", { name: "Run Data Integrations?" })).toBeTruthy();
    expect(screen.getByText("Jan-27 to Mar-27")).toBeTruthy();
    expect(screen.getByText("Revenue.csv")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Approve and run/ }));
    expect(await screen.findByText("Data Integration completed successfully.")).toBeTruthy();
    expect(screen.queryByText("Governed action draft")).toBeNull();
    const inputs = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/agent/conversations/conv-integration/inputs");
    expect(JSON.parse(String(inputs?.[1]?.body))).toEqual({ request_id: "input-integration", values: { start_period: "Jan-27", end_period: "Mar-27", import_mode: "Replace", export_mode: "Merge", file_choice: { source: "upload", upload_token: "integration-upload-1", filename: "Revenue.csv" } } });
  });

  it("runs a saved Planning Data Import from one assistant approval", async () => {
    window.location.hash = "#assistant";
    const assistantUser = {
      ...bootstrap,
      user: { ...bootstrap.user!, permissions: ["agent.use", "operation.execute"] },
      navigation: [...bootstrap.navigation, { code: "assistant", label: "EPM Assistant", path: "/app/agent", group: "Workspace" }]
    };
    const conversation = { conversation_id: "conv-data-import", user_id: 7, title: "Import forecast data", provider: "gemini", model: "gemini-test", created_at: "2026-08-11T10:00:00Z", updated_at: "2026-08-11T10:00:00Z" };
    const message = (id: number, content: string) => ({ message_id: id, conversation_id: conversation.conversation_id, role: "assistant", content, created_at: "2026-08-11T10:01:00Z" });
    const inputRequest = {
      request_id: "input-data-import",
      operation_code: "data-import",
      display_name: "Planning Data Import",
      artifact_name: "Import Forecast Data",
      title: "Choose the Planning data source",
      description: "Choose the file used by the saved Oracle Import Data job.",
      fields: [{ key: "source_file", label: "Data source file", kind: "file_reference", required: true, description: "Configured, Inbox, or upload.", placeholder: "", options: [] }],
      context: { allowed_extensions: [".csv", ".txt", ".zip"] }
    };
    const approval = { request_id: "approval-data-import", operation_code: "data-import", display_name: "Planning Data Import", objective: "Load approved forecast data.", artifact_name: "Import Forecast Data", category: "Data loading", risk_level: "Elevated", route: "/app/operations/data-import", effect: "Start the selected saved Oracle Planning Import Data job.", input_values: { file_source: "Use file configured in Oracle", inbox_file: "", upload_token: "", upload_name: "", error_file_name: "" } };
    let created = false;
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(assistantUser);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/agent/status") return response({ enabled: true, configured: true, provider: "gemini", model: "gemini-test", mode: "governed", message: "Agent is ready." });
      if (url === "/api/v1/agent/conversations" && init?.method === "POST") { created = true; return response({ status: "success", conversation }); }
      if (url === "/api/v1/agent/conversations") return response({ status: "success", conversations: created ? [conversation] : [] });
      if (url === "/api/v1/agent/conversations/conv-data-import/messages" && init?.method === "POST") return response({ status: "success", message: message(2, "Choose the Planning data source."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: inputRequest });
      if (url === "/api/v1/agent/conversations/conv-data-import/inputs" && init?.method === "POST") return response({ status: "success", message: message(3, "Review and approve."), tool_activity: [], action_drafts: [], approval_request: approval, clarification_request: null, input_request: null });
      if (url === "/api/v1/agent/conversations/conv-data-import/approval" && init?.method === "POST") return response({ status: "success", message: message(4, "Planning Data Import queued."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null, execution: { execution_id: "data-import-execution-1", operation_code: "data-import", target_name: "Import Forecast Data", status: "QUEUED" } });
      if (url === "/api/v1/operations/runs/data-import-execution-1") return response({ execution_id: "data-import-execution-1", operation_name: "Planning Data Import - Import Forecast Data", status: "SUCCESS", started_at: "2026-08-11T10:02:00Z", completed_at: "2026-08-11T10:02:05Z", error_message: null, initiated_by: "Finance Planner", trigger_source: "AI_AGENT", steps: [], completed_steps: 3, total_steps: 3, artifacts: [], log_url: null, terminal: true });
      if (url === "/api/v1/agent/conversations/conv-data-import/messages") return response({ status: "success", messages: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null });
      return response({ detail: `Unexpected request: ${url}` }, 404);
    });
    render(<App />);

    fireEvent.change(await screen.findByLabelText("Message the EPM Assistant"), { target: { value: "Import forecast data using Import Forecast Data." } });
    fireEvent.click(screen.getByRole("button", { name: /^Send/ }));
    expect(await screen.findByRole("heading", { name: "Choose the Planning data source" })).toBeTruthy();
    expect(screen.getByText("Import Forecast Data")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Continue to approval/ }));
    expect(await screen.findByRole("heading", { name: "Run Planning Data Import?" })).toBeTruthy();
    expect(screen.getByText("Configured in Oracle")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Approve and run/ }));
    expect(await screen.findByText("Planning Data Import completed successfully.")).toBeTruthy();
    const inputs = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/agent/conversations/conv-data-import/inputs");
    expect(JSON.parse(String(inputs?.[1]?.body))).toEqual({ request_id: "input-data-import", values: { file_choice: { source: "configured" }, error_file_name: "" } });
  });

  it("runs Metadata Import with a conditional Cube Refresh from one assistant approval", async () => {
    window.location.hash = "#assistant";
    const assistantUser = {
      ...bootstrap,
      user: { ...bootstrap.user!, permissions: ["agent.use", "operation.execute"] },
      navigation: [...bootstrap.navigation, { code: "assistant", label: "EPM Assistant", path: "/app/agent", group: "Workspace" }]
    };
    const conversation = { conversation_id: "conv-metadata-import", user_id: 7, title: "Import product metadata", provider: "gemini", model: "gemini-test", created_at: "2026-08-11T10:00:00Z", updated_at: "2026-08-11T10:00:00Z" };
    const message = (id: number, content: string) => ({ message_id: id, conversation_id: conversation.conversation_id, role: "assistant", content, created_at: "2026-08-11T10:01:00Z" });
    const inputRequest = { request_id: "input-metadata", operation_code: "metadata-import", display_name: "Metadata Import", artifact_name: "Import Products", title: "Choose the metadata file and post-import action", description: "Choose the source and optional refresh.", fields: [], context: { allowed_extensions: [".csv", ".zip"], refresh_jobs: [] } };
    const approval = { request_id: "approval-metadata", operation_code: "metadata-import", display_name: "Metadata Import", objective: "Import product hierarchy.", artifact_name: "Import Products", category: "Application administration", risk_level: "Elevated", route: "/app/operations/metadata-import", effect: "Start Metadata Import and refresh after success.", input_values: { file_source: "Upload on governed screen", inbox_file: "", upload_token: "metadata-upload-1", upload_name: "Products.csv", error_file_name: "Metadata_Errors.csv", refresh_after_import: true, refresh_job_name: "Refresh_Cube" } };
    let created = false;
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(assistantUser);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/agent/status") return response({ enabled: true, configured: true, provider: "gemini", model: "gemini-test", mode: "governed", message: "Agent is ready." });
      if (url === "/api/v1/agent/conversations" && init?.method === "POST") { created = true; return response({ status: "success", conversation }); }
      if (url === "/api/v1/agent/conversations") return response({ status: "success", conversations: created ? [conversation] : [] });
      if (url === "/api/v1/agent/conversations/conv-metadata-import/messages" && init?.method === "POST") return response({ status: "success", message: message(2, "Choose the metadata file."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: inputRequest });
      if (url.startsWith("/api/v1/uploads?filename=") && init?.method === "POST") return response({ status: "success", upload: { token: "metadata-upload-1", filename: "Products.csv", size: 24 } });
      if (url === "/api/v1/agent/conversations/conv-metadata-import/inputs" && init?.method === "POST") return response({ status: "success", message: message(3, "Review and approve."), tool_activity: [], action_drafts: [], approval_request: approval, clarification_request: null, input_request: null });
      if (url === "/api/v1/agent/conversations/conv-metadata-import/approval" && init?.method === "POST") return response({ status: "success", message: message(4, "Metadata Import queued."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null, execution: { execution_id: "metadata-execution-1", operation_code: "metadata-import", target_name: "Import Products", status: "QUEUED" } });
      if (url === "/api/v1/operations/runs/metadata-execution-1") return response({ execution_id: "metadata-execution-1", operation_name: "Metadata Import - Import Products", status: "SUCCESS", started_at: "2026-08-11T10:02:00Z", completed_at: "2026-08-11T10:02:05Z", error_message: null, initiated_by: "Planning Administrator", trigger_source: "AI_AGENT", steps: [], completed_steps: 4, total_steps: 4, artifacts: [], log_url: null, terminal: true });
      if (url === "/api/v1/agent/conversations/conv-metadata-import/messages") return response({ status: "success", messages: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null });
      return response({ detail: `Unexpected request: ${url}` }, 404);
    });
    render(<App />);

    fireEvent.change(await screen.findByLabelText("Message the EPM Assistant"), { target: { value: "Import product metadata using Import Products." } });
    fireEvent.click(screen.getByRole("button", { name: /^Send/ }));
    expect(await screen.findByRole("heading", { name: "Choose the metadata file and post-import action" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Metadata Import local file"), { target: { files: [new File(["Product,Alias\nP100,Phone"], "Products.csv", { type: "text/csv" })] } });
    fireEvent.click(screen.getByLabelText("Refresh cube after Metadata Import"));
    expect(screen.getByText(/exact saved job name/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Metadata Import Cube Refresh job"), { target: { value: "Refresh_Cube" } });
    fireEvent.change(screen.getByLabelText("Metadata Import error output filename"), { target: { value: "Metadata_Errors.csv" } });
    fireEvent.click(screen.getByRole("button", { name: /Continue to approval/ }));
    expect(await screen.findByRole("heading", { name: "Run Metadata Import?" }, { timeout: 5000 })).toBeTruthy();
    expect(screen.getByText("Run Refresh_Cube")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Approve and run/ }));
    expect(await screen.findByText("Metadata Import completed successfully.")).toBeTruthy();
    const inputs = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/agent/conversations/conv-metadata-import/inputs");
    expect(JSON.parse(String(inputs?.[1]?.body))).toEqual({ request_id: "input-metadata", values: { file_choice: { source: "upload", upload_token: "metadata-upload-1", filename: "Products.csv" }, error_file_name: "Metadata_Errors.csv", refresh_after_import: true, refresh_job_name: "Refresh_Cube" } });
  });

  it("runs an exact saved Cube Refresh job from one assistant approval", async () => {
    window.location.hash = "#assistant";
    const assistantUser = {
      ...bootstrap,
      user: { ...bootstrap.user!, permissions: ["agent.use", "operation.execute"] },
      navigation: [...bootstrap.navigation, { code: "assistant", label: "EPM Assistant", path: "/app/agent", group: "Workspace" }]
    };
    const conversation = { conversation_id: "conv-cube-refresh", user_id: 7, title: "Refresh the Planning cube", provider: "gemini", model: "gemini-test", created_at: "2026-08-11T10:00:00Z", updated_at: "2026-08-11T10:00:00Z" };
    const message = (id: number, content: string) => ({ message_id: id, conversation_id: conversation.conversation_id, role: "assistant", content, created_at: "2026-08-11T10:01:00Z" });
    const approval = { request_id: "approval-cube-refresh", operation_code: "cube-refresh", display_name: "Planning Cube Refresh", objective: "Synchronize Planning metadata with the underlying cube.", artifact_name: "Refresh_Cube", category: "Application administration", risk_level: "Elevated", route: "/app/operations/cube-refresh", effect: "Start the exact saved application-wide Cube Refresh job in Oracle, synchronize Planning metadata with the underlying cube, and monitor the operation to completion.", input_values: {} };
    let created = false;
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(assistantUser);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/agent/status") return response({ enabled: true, configured: true, provider: "gemini", model: "gemini-test", mode: "governed", message: "Agent is ready." });
      if (url === "/api/v1/agent/conversations" && init?.method === "POST") { created = true; return response({ status: "success", conversation }); }
      if (url === "/api/v1/agent/conversations") return response({ status: "success", conversations: created ? [conversation] : [] });
      if (url === "/api/v1/agent/conversations/conv-cube-refresh/messages" && init?.method === "POST") return response({ status: "success", message: message(2, "Review the exact saved Cube Refresh job before it runs."), tool_activity: [], action_drafts: [], approval_request: approval, clarification_request: null, input_request: null });
      if (url === "/api/v1/agent/conversations/conv-cube-refresh/approval" && init?.method === "POST") return response({ status: "success", message: message(3, "Cube Refresh queued."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null, execution: { execution_id: "cube-refresh-execution-1", operation_code: "cube-refresh", target_name: "Refresh_Cube", status: "QUEUED" } });
      if (url === "/api/v1/operations/runs/cube-refresh-execution-1") return response({ execution_id: "cube-refresh-execution-1", operation_name: "Planning Cube Refresh - Refresh_Cube", status: "SUCCESS", started_at: "2026-08-11T10:02:00Z", completed_at: "2026-08-11T10:02:05Z", error_message: null, initiated_by: "Planning Administrator", trigger_source: "AI_AGENT", steps: [], completed_steps: 3, total_steps: 3, artifacts: [], log_url: null, terminal: true });
      if (url === "/api/v1/agent/conversations/conv-cube-refresh/messages") return response({ status: "success", messages: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null });
      return response({ detail: `Unexpected request: ${url}` }, 404);
    });
    render(<App />);

    fireEvent.change(await screen.findByLabelText("Message the EPM Assistant"), { target: { value: "Run the saved Refresh_Cube job." } });
    fireEvent.click(screen.getByRole("button", { name: /^Send/ }));
    expect(await screen.findByRole("heading", { name: "Run Planning Cube Refresh?" })).toBeTruthy();
    expect(screen.getByText("Application-wide impact")).toBeTruthy();
    expect(screen.getAllByText("Refresh_Cube").length).toBeGreaterThan(0);
    expect(screen.getByText("This approval starts an application-wide Cube Refresh.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Approve and run/ }));
    expect(await screen.findByText("Cube Refresh completed successfully.")).toBeTruthy();
  });

  it("updates a live substitution variable through one protected assistant approval", async () => {
    window.location.hash = "#assistant";
    const assistantUser = {
      ...bootstrap,
      user: { ...bootstrap.user!, permissions: ["agent.use", "operation.execute", "variable.update"] },
      navigation: [...bootstrap.navigation, { code: "assistant", label: "EPM Assistant", path: "/app/agent", group: "Workspace" }]
    };
    const conversation = { conversation_id: "conv-variable", user_id: 7, title: "Update the Planning year", provider: "gemini", model: "gemini-test", created_at: "2026-08-11T10:00:00Z", updated_at: "2026-08-11T10:00:00Z" };
    const message = (id: number, content: string) => ({ message_id: id, conversation_id: conversation.conversation_id, role: "assistant", content, created_at: "2026-08-11T10:01:00Z" });
    const inputRequest = { request_id: "input-variable", operation_code: "substitution-variables", display_name: "Substitution Variables", artifact_name: "CurYr", title: "Enter the new substitution variable value", description: "Review the live scope and current Oracle value before entering the replacement value.", fields: [{ key: "new_value", label: "New value", kind: "text", required: true, description: "Exact Planning member or text value.", placeholder: "FY26", options: [] }], context: { action: "UPDATE", scope: "ALL", variable_name: "CurYr", current_value: "FY26", prefill: { new_value: "FY27" } } };
    const approval = { request_id: "approval-variable", operation_code: "substitution-variables", display_name: "Substitution Variables", objective: "Move the current Planning year to FY27.", artifact_name: "CurYr", category: "Application administration", risk_level: "Elevated", route: "/app/operations/substitution-variables", effect: "Apply exactly one reviewed substitution-variable change in Oracle and verify the resulting value.", input_values: { action: "UPDATE", scope: "ALL", variable_name: "CurYr", new_value: "FY27", expected_current_value: "FY26", create_if_missing: false } };
    let created = false;
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(assistantUser);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/agent/status") return response({ enabled: true, configured: true, provider: "gemini", model: "gemini-test", mode: "governed", message: "Agent is ready." });
      if (url === "/api/v1/agent/conversations" && init?.method === "POST") { created = true; return response({ status: "success", conversation }); }
      if (url === "/api/v1/agent/conversations") return response({ status: "success", conversations: created ? [conversation] : [] });
      if (url === "/api/v1/agent/conversations/conv-variable/messages" && init?.method === "POST") return response({ status: "success", message: message(2, "Review the live variable value."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: inputRequest });
      if (url === "/api/v1/agent/conversations/conv-variable/inputs" && init?.method === "POST") return response({ status: "success", message: message(3, "Review and approve the exact change."), tool_activity: [], action_drafts: [], approval_request: approval, clarification_request: null, input_request: null });
      if (url === "/api/v1/agent/conversations/conv-variable/approval" && init?.method === "POST") return response({ status: "success", message: message(4, "Variable update queued."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null, execution: { execution_id: "variable-execution-1", operation_code: "substitution-variables", target_name: "ALL.CurYr", status: "QUEUED" } });
      if (url === "/api/v1/operations/runs/variable-execution-1") return response({ execution_id: "variable-execution-1", operation_name: "Substitution Variables - ALL.CurYr", status: "SUCCESS", started_at: "2026-08-11T10:02:00Z", completed_at: "2026-08-11T10:02:03Z", error_message: null, initiated_by: "Planning Administrator", trigger_source: "AI_AGENT", steps: [], completed_steps: 3, total_steps: 3, artifacts: [], log_url: null, terminal: true });
      if (url === "/api/v1/agent/conversations/conv-variable/messages") return response({ status: "success", messages: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null });
      return response({ detail: `Unexpected request: ${url}` }, 404);
    });
    render(<App />);

    fireEvent.change(await screen.findByLabelText("Message the EPM Assistant"), { target: { value: "Update CurYr to FY27." } });
    fireEvent.click(screen.getByRole("button", { name: /^Send/ }));
    expect(await screen.findByRole("heading", { name: "Enter the new substitution variable value" })).toBeTruthy();
    expect(screen.getByText("Current Oracle value")).toBeTruthy();
    expect(screen.getByText("Protected update.")).toBeTruthy();
    expect((screen.getByLabelText("Agent substitution variable new value") as HTMLInputElement).value).toBe("FY27");
    const continueButton = screen.getByRole("button", { name: /Continue to approval/ });
    await waitFor(() => expect((continueButton as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(continueButton);
    expect(await screen.findByRole("heading", { name: "Run Substitution Variables?" })).toBeTruthy();
    expect(screen.getByText("This approval changes one Oracle substitution variable.")).toBeTruthy();
    expect(screen.getByText("ALL.CurYr")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Approve and run/ }));
    expect(await screen.findByText("Substitution Variable change completed successfully.")).toBeTruthy();
    const inputs = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/agent/conversations/conv-variable/inputs");
    expect(JSON.parse(String(inputs?.[1]?.body))).toEqual({ request_id: "input-variable", values: { new_value: "FY27" } });
  });

  it("prefills an identified user variable and member before approval", async () => {
    window.location.hash = "#assistant";
    const assistantUser = {
      ...bootstrap,
      user: { ...bootstrap.user!, username: "planner", permissions: ["agent.use", "operation.execute", "user_variable.update"] },
      navigation: [...bootstrap.navigation, { code: "assistant", label: "EPM Assistant", path: "/app/agent", group: "Workspace" }]
    };
    const conversation = { conversation_id: "conv-user-variable", user_id: 7, title: "Update my entity", provider: "gemini", model: "gemini-test", created_at: "2026-08-11T10:00:00Z", updated_at: "2026-08-11T10:00:00Z" };
    const message = (id: number, content: string) => ({ message_id: id, conversation_id: conversation.conversation_id, role: "assistant", content, created_at: "2026-08-11T10:01:00Z" });
    const inputRequest = { request_id: "input-user-variable", operation_code: "user-variables", display_name: "User Variables", artifact_name: "MyEntity", title: "Choose the user's Planning context", description: "Confirm the Oracle user and exact Entity member.", fields: [], context: { variable_name: "MyEntity", dimension: "Entity", default_user: "planner", can_manage_users: false, prefill: { new_member: "Sales East" } } };
    const approval = { request_id: "approval-user-variable", operation_code: "user-variables", display_name: "User Variables", objective: "Set the MyEntity user variable to Sales East.", artifact_name: "MyEntity", category: "Application administration", risk_level: "Elevated", route: "/app/operations/user-variables", effect: "Apply exactly one reviewed user-variable member assignment for the selected Oracle user and verify the resulting value.", input_values: { user_name: "planner", variable_name: "MyEntity", dimension: "Entity", new_member: "Sales East", expected_current_member: "Sales West" } };
    let created = false;
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(assistantUser);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/agent/status") return response({ enabled: true, configured: true, provider: "gemini", model: "gemini-test", mode: "governed", message: "Agent is ready." });
      if (url === "/api/v1/agent/conversations" && init?.method === "POST") { created = true; return response({ status: "success", conversation }); }
      if (url === "/api/v1/agent/conversations") return response({ status: "success", conversations: created ? [conversation] : [] });
      if (url === "/api/v1/agent/conversations/conv-user-variable/messages" && init?.method === "POST") return response({ status: "success", message: message(2, "Review the live user-variable assignment."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: inputRequest });
      if (url === "/api/v1/agent/conversations/conv-user-variable/inputs" && init?.method === "POST") return response({ status: "success", message: message(3, "Review and approve the exact assignment."), tool_activity: [], action_drafts: [], approval_request: approval, clarification_request: null, input_request: null });
      if (url === "/api/v1/agent/conversations/conv-user-variable/approval" && init?.method === "POST") return response({ status: "success", message: message(4, "User Variable update queued."), tool_activity: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null, execution: { execution_id: "user-variable-execution-1", operation_code: "user-variables", target_name: "planner.MyEntity", status: "QUEUED" } });
      if (url === "/api/v1/operations/runs/user-variable-execution-1") return response({ execution_id: "user-variable-execution-1", operation_name: "User Variables - planner.MyEntity", status: "SUCCESS", started_at: "2026-08-11T10:02:00Z", completed_at: "2026-08-11T10:02:03Z", error_message: null, initiated_by: "Planning User", trigger_source: "AI_AGENT", steps: [], completed_steps: 3, total_steps: 3, artifacts: [], log_url: null, terminal: true });
      if (url === "/api/v1/agent/conversations/conv-user-variable/messages") return response({ status: "success", messages: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null });
      return response({ detail: `Unexpected request: ${url}` }, 404);
    });
    render(<App />);

    fireEvent.change(await screen.findByLabelText("Message the EPM Assistant"), { target: { value: "Set the MyEntity user variable to Sales East." } });
    fireEvent.click(screen.getByRole("button", { name: /^Send/ }));
    expect(await screen.findByRole("heading", { name: "Choose the user's Planning context" })).toBeTruthy();
    expect((screen.getByLabelText("Agent Oracle user") as HTMLInputElement).value).toBe("planner");
    expect((screen.getByLabelText("Agent user variable new member") as HTMLInputElement).value).toBe("Sales East");
    fireEvent.click(screen.getByRole("button", { name: /Continue to approval/ }));
    expect(await screen.findByRole("heading", { name: "Run User Variables?" })).toBeTruthy();
    expect(screen.getAllByText("planner").length).toBeGreaterThan(0);
    expect(screen.getAllByText("MyEntity").length).toBeGreaterThan(0);
    expect(screen.getByText("Sales East")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Approve and run/ }));
    expect(await screen.findByText("User Variable change completed successfully.")).toBeTruthy();
    const inputs = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/agent/conversations/conv-user-variable/inputs");
    expect(JSON.parse(String(inputs?.[1]?.body))).toEqual({ request_id: "input-user-variable", values: { user_name: "planner", new_member: "Sales East" } });
  });

  it("generates and downloads a saved-view Excel output from the restricted Data Explorer", async () => {
    window.location.hash = "#reports";
    const reportUser = {
      ...bootstrap,
      user: { ...bootstrap.user!, permissions: ["report.generate"] },
      navigation: [...bootstrap.navigation, { code: "reports", label: "Data Explorer", path: "/app/reports", group: "Planning" }]
    };
    const preflight = {
      form_name: "Revenue Forecast",
      title: "Revenue Forecast Report",
      cube: "Plan1",
      registered: true,
      page_dimensions: ["Year", "Scenario"],
      row_dimensions: ["Account"],
      column_dimensions: ["Period"],
      current_pov: [["Year", "FY25"], ["Scenario", "Forecast"]],
      allowed_page_members: [["Year", ["FY25", "FY26"]], ["Scenario", ["Forecast", "Budget"]]]
    };
    const execution = {
      execution_id: "report-execution-1",
      operation_name: "Report Generation - Revenue Forecast",
      status: "SUCCESS",
      started_at: "2026-08-11T11:00:00Z",
      completed_at: "2026-08-11T11:00:04Z",
      error_message: null,
      initiated_by: "Finance Planner",
      trigger_source: "MANUAL",
      steps: [],
      completed_steps: 1,
      total_steps: 1,
      artifacts: [{ name: "Revenue_Forecast_Report.xlsx", url: "/app/reports/Revenue_Forecast_Report.xlsx" }],
      log_url: null,
      terminal: true
    };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(reportUser);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/reports/catalog") return response({ status: "success", reports: [{ name: "Revenue Forecast", title: "Revenue Forecast Report", cube: "Plan1", default_pov: [["Year", "FY25"], ["Scenario", "Forecast"]] }] });
      if (url === "/api/v1/reports/preflight" && init?.method === "POST") return response({ status: "success", preflight });
      if (url === "/api/v1/operations/reports/runs" && init?.method === "POST") return response({ status: "accepted", execution_id: execution.execution_id, redirect: `/app/operations/runs/${execution.execution_id}` }, 202);
      if (url === `/api/v1/operations/runs/${execution.execution_id}`) return response(execution);
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Data Explorer" })).toBeTruthy();
    expect(screen.queryByLabelText("Planning form name or ID")).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: /Inspect layout/ }));
    fireEvent.change(await screen.findByLabelText("Report POV Year"), { target: { value: "FY26" } });
    fireEvent.click(screen.getByRole("button", { name: /Review output/ }));
    expect(await screen.findByRole("heading", { name: "Review the Excel output" })).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/I reviewed the source/));
    fireEvent.click(screen.getByRole("button", { name: /^Generate Excel/ }));

    expect(await screen.findByRole("heading", { name: "Operation completed successfully" })).toBeTruthy();
    const download = screen.getByRole("link", { name: /Revenue_Forecast_Report.xlsx/ });
    expect(download.getAttribute("href")).toBe("/app/reports/Revenue_Forecast_Report.xlsx");
    const submitted = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/operations/reports/runs");
    expect(JSON.parse(String(submitted?.[1]?.body))).toEqual({ form_name: "Revenue Forecast", title: "Revenue Forecast Report", page_member_overrides: { Year: "FY26", Scenario: "Forecast" } });
    expect(new Headers(submitted?.[1]?.headers).get("X-CSRF-Token")).toBe("test-csrf");
  });

  it("keeps the unsupported Planning-form workflow out of the restricted Data Explorer", async () => {
    window.location.hash = "#reports";
    const reportUser = {
      ...bootstrap,
      user: { ...bootstrap.user!, permissions: ["report.generate"] },
      navigation: [...bootstrap.navigation, { code: "reports", label: "Data Explorer", path: "/app/reports", group: "Planning" }]
    };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(reportUser);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/reports/catalog") return response({ status: "success", reports: [] });
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Data Explorer" })).toBeTruthy();
    expect(screen.getByText("No saved views are available for this account.")).toBeTruthy();
    expect(screen.queryByLabelText("Planning form name or ID")).toBeNull();
    expect(screen.queryByRole("button", { name: "Register report" })).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/v1/reports/preflight")).toBe(false);
  });

  it("renders a live Data Explorer grid returned by the EPM Assistant", async () => {
    window.location.hash = "#assistant";
    const assistantUser = {
      ...bootstrap,
      user: { ...bootstrap.user!, permissions: ["agent.use", "data.review"] },
      navigation: [...bootstrap.navigation, { code: "assistant", label: "EPM Assistant", path: "/app/agent", group: "Workspace" }]
    };
    const conversation = { conversation_id: "conv-review", user_id: 7, title: "Review forecast data", provider: "groq", model: "test", created_at: "2026-08-19T10:00:00Z", updated_at: "2026-08-19T10:00:00Z" };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(assistantUser);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/agent/status") return response({ enabled: true, configured: true, provider: "groq", model: "test", mode: "governed", message: "Ready" });
      if (url === "/api/v1/agent/conversations") return response({ status: "success", conversations: [conversation] });
      if (url === "/api/v1/agent/conversations/conv-review/messages" && init?.method === "POST") return response({
        status: "success",
        message: { message_id: 2, conversation_id: "conv-review", role: "assistant", content: "Here is the live Planning data.", created_at: "2026-08-19T10:01:00Z" },
        tool_activity: [{
          name: "review_data_slice",
          arguments: {},
          status: "SUCCESS",
          summary: "Read-only Planning data returned.",
          result: {
            cube: "Plan1",
            name: "Data Review",
            row_count: 1,
            column_count: 2,
            cell_count: 2,
            missing_cell_count: 0,
            returned_row_count: 1,
            truncated: false,
            request: { cube: "Plan1", pov: { Scenario: "Forecast", Year: "FY27" }, rows: [{ dimension: "Account", members: ["Revenue"] }], columns: [{ dimension: "Period", members: ["Jan", "Feb"] }] },
            grid: { row_dimensions: ["Account"], column_dimensions: ["Period"], columns: [["Jan"], ["Feb"]], rows: [{ headers: ["Revenue"], data: [1200, 1350] }], pov: [["Scenario", "Forecast"], ["Year", "FY27"]] }
          }
        }],
        action_drafts: [], approval_request: null, clarification_request: null, input_request: null
      });
      if (url === "/api/v1/agent/conversations/conv-review/messages") return response({ status: "success", messages: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null });
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    fireEvent.change(await screen.findByLabelText("Message the EPM Assistant"), { target: { value: "Show Plan1 revenue for Jan and Feb." } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("heading", { name: "Plan1 data explorer" })).toBeTruthy();
    expect(screen.getByText("1,200")).toBeTruthy();
    expect(screen.getByText("1,350")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Export Excel/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Export CSV/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Open Data Explorer/ })).toBeTruthy();
    expect(screen.queryByText(/groq\s*·\s*test/i)).toBeNull();
    expect(document.querySelector(".app-shell--assistant")).toBeTruthy();
  });

  it("lets the EPM Assistant reopen a saved Data Explorer view with one choice", async () => {
    window.location.hash = "#assistant";
    const assistantUser = {
      ...bootstrap,
      user: { ...bootstrap.user!, permissions: ["agent.use", "data.review"] },
      navigation: [...bootstrap.navigation, { code: "assistant", label: "EPM Assistant", path: "/app/agent", group: "Workspace" }]
    };
    const conversation = { conversation_id: "conv-saved-view", user_id: 7, title: "Explore saved data", provider: "groq", model: "test", created_at: "2026-08-19T10:00:00Z", updated_at: "2026-08-19T10:00:00Z" };
    let sent = 0;
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(assistantUser);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/agent/status") return response({ enabled: true, configured: true, provider: "groq", model: "test", mode: "governed", message: "Ready" });
      if (url === "/api/v1/agent/conversations") return response({ status: "success", conversations: [conversation] });
      if (url === "/api/v1/agent/conversations/conv-saved-view/messages" && init?.method === "POST") {
        sent += 1;
        if (sent === 1) return response({
          status: "success",
          message: { message_id: 2, conversation_id: "conv-saved-view", role: "assistant", content: "Choose a saved view.", created_at: "2026-08-19T10:01:00Z" },
          tool_activity: [{ name: "list_data_explorer_views", arguments: {}, status: "SUCCESS", summary: "Saved views returned.", result: { count: 1, total_count: 1, truncated: false, views: [{ name: "revenue-forecast", title: "Revenue Forecast", cube: "Plan1", pov: [{ dimension: "Scenario", member: "Forecast" }], row_dimensions: ["Account"], column_dimensions: ["Period"] }] } }],
          action_drafts: [], approval_request: null, clarification_request: null, input_request: null
        });
        return response({
          status: "success",
          message: { message_id: 3, conversation_id: "conv-saved-view", role: "assistant", content: "Current Oracle values loaded.", created_at: "2026-08-19T10:02:00Z" },
          tool_activity: [{ name: "review_saved_data_view", arguments: { name: "revenue-forecast" }, status: "SUCCESS", summary: "Saved view loaded.", result: { cube: "Plan1", name: "Plan1 data slice", saved_view: { name: "revenue-forecast", title: "Revenue Forecast" }, row_count: 1, column_count: 1, cell_count: 1, missing_cell_count: 0, returned_row_count: 1, truncated: false, request: { cube: "Plan1", pov: { Scenario: "Forecast" }, rows: [{ dimension: "Account", members: ["Revenue"] }], columns: [{ dimension: "Period", members: ["Jan"] }] }, grid: { row_dimensions: ["Account"], column_dimensions: ["Period"], columns: [["Jan"]], rows: [{ headers: ["Revenue"], data: [1200] }], pov: [["Scenario", "Forecast"]] } } }],
          action_drafts: [], approval_request: null, clarification_request: null, input_request: null
        });
      }
      if (url === "/api/v1/agent/conversations/conv-saved-view/messages") return response({ status: "success", messages: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null });
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    fireEvent.change(await screen.findByLabelText("Message the EPM Assistant"), { target: { value: "Show my saved Data Explorer views." } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    fireEvent.click(await screen.findByRole("button", { name: /Revenue Forecast/ }));

    expect(await screen.findByRole("heading", { name: "Revenue Forecast" })).toBeTruthy();
    expect(screen.getByText("1,200")).toBeTruthy();
    const secondRequest = fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith("/messages") && init?.method === "POST")[1];
    expect(JSON.parse(String(secondRequest?.[1]?.body)).content).toBe("Use saved Data Explorer view revenue-forecast and load its current Oracle data.");
  });

  it("runs a live variance review from a selected saved layout", async () => {
    window.location.hash = "#assistant";
    const assistantUser = {
      ...bootstrap,
      user: { ...bootstrap.user!, permissions: ["agent.use", "data.review"] },
      navigation: [...bootstrap.navigation, { code: "assistant", label: "EPM Assistant", path: "/app/agent", group: "Workspace" }]
    };
    const conversation = { conversation_id: "conv-variance", user_id: 7, title: "Review variance", provider: "groq", model: "test", created_at: "2026-08-19T10:00:00Z", updated_at: "2026-08-19T10:00:00Z" };
    let sent = 0;
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(assistantUser);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/agent/status") return response({ enabled: true, configured: true, provider: "groq", model: "test", mode: "governed", message: "Ready" });
      if (url === "/api/v1/agent/conversations") return response({ status: "success", conversations: [conversation] });
      if (url === "/api/v1/agent/conversations/conv-variance/messages" && init?.method === "POST") {
        sent += 1;
        if (sent === 1) return response({
          status: "success",
          message: { message_id: 2, conversation_id: "conv-variance", role: "assistant", content: "Choose the scope to analyze.", created_at: "2026-08-19T10:01:00Z" },
          tool_activity: [{ name: "list_variance_views", arguments: { comparison: "Actual vs Budget", period: "Sep", year: "FY26", threshold: 500 }, status: "SUCCESS", summary: "Saved variance layouts returned.", result: { purpose: "variance", comparison: "Actual vs Budget", period: "Sep", year: "FY26", threshold: 500, count: 1, total_count: 1, truncated: false, views: [{ name: "monthly-variance", title: "Monthly Variance", cube: "Plan1", pov: [{ dimension: "Scenario", member: "Actual" }, { dimension: "Year", member: "FY26" }], row_dimensions: ["Account"], column_dimensions: ["Period"] }] } }],
          action_drafts: [], approval_request: null, clarification_request: null, input_request: null
        });
        return response({
          status: "success",
          message: { message_id: 3, conversation_id: "conv-variance", role: "assistant", content: "The live variance review is ready.", created_at: "2026-08-19T10:02:00Z" },
          tool_activity: [{ name: "review_saved_variance", arguments: { name: "monthly-variance", comparison: "Actual vs Budget", period: "Sep", year: "FY26", threshold: 500 }, status: "SUCCESS", summary: "Variance compared.", result: { source_cube: "Plan1", target_cube: "Plan1", saved_view: { name: "monthly-variance", title: "Monthly Variance" }, variance_context: { comparison: "Actual vs Budget", period: "Sep", year: "FY26", threshold: 500 }, source_request: { cube: "Plan1", pov: { Scenario: "Actual", Year: "FY26", Product: "BaseData" }, rows: [{ dimension: "Account", members: ["Revenue"] }], columns: [{ dimension: "Period", members: ["Sep"] }] }, target_request: { cube: "Plan1", pov: { Scenario: "Budget", Year: "FY26", Product: "BaseData" }, rows: [{ dimension: "Account", members: ["Revenue"] }], columns: [{ dimension: "Period", members: ["Sep"] }] }, result: { source_form: "Actual Sep", target_form: "Budget Sep", compared_cells: 1, matched_cells: 0, tolerance: 500, mismatches: [{ row_headers: ["Revenue"], column_headers: ["Sep"], source_value: 2000, target_value: 1000, difference: 1000 }] } } }],
          action_drafts: [], approval_request: null, clarification_request: null, input_request: null
        });
      }
      if (url === "/api/v1/agent/conversations/conv-variance/messages") return response({ status: "success", messages: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null });
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    fireEvent.change(await screen.findByLabelText("Message the EPM Assistant"), { target: { value: "Show September Actual vs Budget variance above 500 for FY26." } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    fireEvent.click(await screen.findByRole("button", { name: /Monthly Variance/ }));

    expect(await screen.findByRole("heading", { name: "Actual vs Budget · Sep · FY26" })).toBeTruthy();
    expect(screen.getAllByText("1,000").length).toBeGreaterThan(0);
    expect(screen.getByRole("region", { name: "Comparison POV" })).toBeTruthy();
    expect(screen.getAllByText("BaseData").length).toBe(2);
    const secondRequest = fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith("/messages") && init?.method === "POST")[1];
    expect(JSON.parse(String(secondRequest?.[1]?.body)).content).toBe("Use saved Data Explorer view `monthly-variance` for the variance review.");

    fireEvent.click(screen.getByRole("button", { name: "Refine comparison" }));
    fireEvent.change(screen.getByLabelText("POV Product"), { target: { value: "Snacks" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply refinement" }));

    await waitFor(() => expect(fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith("/messages") && init?.method === "POST")).toHaveLength(3));
    const thirdRequest = fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith("/messages") && init?.method === "POST")[2];
    expect(JSON.parse(String(thirdRequest?.[1]?.body)).content).toBe("Refine variance comparison using saved Data Explorer view `monthly-variance`. Keep Actual vs Budget for Sep FY26 with threshold 500. POV overrides: Product=`Snacks`.");
  });

  it("keeps the selected agent cube when Oracle dimension discovery is unavailable", async () => {
    window.location.hash = "#assistant";
    const assistantUser = {
      ...bootstrap,
      user: { ...bootstrap.user!, permissions: ["agent.use", "data.review"] },
      navigation: [...bootstrap.navigation, { code: "assistant", label: "EPM Assistant", path: "/app/agent", group: "Workspace" }]
    };
    const conversation = { conversation_id: "conv-cube", user_id: 7, title: "Review Plan2", provider: "groq", model: "test", created_at: "2026-08-19T10:00:00Z", updated_at: "2026-08-19T10:00:00Z" };
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/bootstrap") return response(assistantUser);
      if (url === "/api/v1/home") return response(home);
      if (url === "/api/v1/notifications") return response(notificationInbox);
      if (url === "/api/v1/agent/status") return response({ enabled: true, configured: true, provider: "groq", model: "test", mode: "governed", message: "Ready" });
      if (url === "/api/v1/agent/conversations") return response({ status: "success", conversations: [conversation] });
      if (url === "/api/v1/agent/conversations/conv-cube/messages" && init?.method === "POST") return response({
        status: "success",
        message: { message_id: 2, conversation_id: "conv-cube", role: "assistant", content: "Plan2 remains selected.", created_at: "2026-08-19T10:01:00Z" },
        tool_activity: [{
          name: "list_cube_dimensions",
          arguments: { cube: "Plan2" },
          status: "FAILED",
          summary: "Dimension discovery is unavailable.",
          result: { error: "Oracle returned no discoverable dimensions for cube 'Plan2'." }
        }],
        action_drafts: [], approval_request: null, clarification_request: null, input_request: null
      });
      if (url === "/api/v1/agent/conversations/conv-cube/messages") return response({ status: "success", messages: [], action_drafts: [], approval_request: null, clarification_request: null, input_request: null });
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<App />);

    const composer = await screen.findByLabelText("Message the EPM Assistant");
    fireEvent.change(composer, { target: { value: "Use cube Plan2 for this data review and help me choose the remaining POV, rows, and columns." } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("heading", { name: "Plan2 remains selected" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Where should I review data?" })).toBeNull();
    fireEvent.change(screen.getByLabelText("POV mappings"), { target: { value: "Scenario=Actual\nVersion=Working\nEntity=No Entity\nYear=FY24" } });
    fireEvent.change(screen.getByLabelText("Row mappings"), { target: { value: "Account=Units|Average_Selling_Price|Total_Revenue" } });
    fireEvent.change(screen.getByLabelText("Column mappings"), { target: { value: "Period=Jan|Feb|Mar" } });
    fireEvent.click(screen.getByRole("button", { name: /Use this exact layout/ }));

    expect((screen.getByLabelText("Message the EPM Assistant") as HTMLTextAreaElement).value).toContain("Cube: Plan2");
    expect((screen.getByLabelText("Message the EPM Assistant") as HTMLTextAreaElement).value).toContain("Scenario=Actual");
    expect((screen.getByLabelText("Message the EPM Assistant") as HTMLTextAreaElement).value).toContain("Account=Units|Average_Selling_Price|Total_Revenue");
  });

  it("shows the sign-in experience when there is no session", async () => {
    vi.mocked(fetch).mockImplementation(() => response({ ...bootstrap, authenticated: false, user: null }));
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Welcome back" })).toBeTruthy();
    expect(screen.getByLabelText("Local platform username")).toBeTruthy();
  });
});
