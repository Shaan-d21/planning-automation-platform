import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SchedulingWorkspace } from "./SchedulingWorkspace";

const pipelineCatalog = {
  status: "success",
  pipelines: [{ code: "PIPE01", name: "Monthly Revenue Forecast", description: null }],
  artifacts: []
};

const pipelinePreview = {
  status: "success",
  preview: {
    code: "PIPE01",
    display_name: "Monthly Revenue Forecast",
    variables: [{ name: "YEAR", display_name: "Planning year", default_value: "FY27", required: true, editable: true }],
    file_requirements: [],
    stages: [{ name: "LOAD", display_name: "Load and calculate", job_count: 2, runs_in_parallel: false }]
  }
};

const schedule = {
  schedule_id: 11,
  name: "Monthly Revenue Forecast · Monthly",
  target_type: "ORACLE_PIPELINE",
  target_key: "PIPE01",
  frequency: "MONTHLY",
  timezone: "Asia/Kolkata",
  first_run_local: "2027-03-01T08:00:00",
  input_policy: "ORACLE_DEFAULTS",
  variables: {},
  inbox_files: {},
  misfire_policy: "RUN_ONCE",
  concurrency_policy: "SKIP_IF_ACTIVE",
  enabled: true,
  next_run_at: "2027-03-01T02:30:00+00:00",
  created_at: "2026-08-01T00:00:00+00:00",
  updated_at: "2026-08-01T00:00:00+00:00",
  last_triggered_at: null,
  last_execution_id: null,
  last_outcome: "NEVER",
  last_error: null
};

function response(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

const emptyHistory = {
  status: "success",
  summary: { total: 0, submitted: 0, completed: 0, failed: 0, skipped: 0, claimed: 0 },
  runs: []
};

describe("SchedulingWorkspace", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/operations/pipelines/catalog") return response(pipelineCatalog);
      if (url === "/api/v1/schedules") return response({ status: "success", schedules: [schedule] });
      if (url === "/api/v1/schedules/runs/history") return response(emptyHistory);
      return response({ detail: "Unexpected request" }, 404);
    }));
  });

  it("shows Pipeline-first schedules and their next occurrence", async () => {
    render(<SchedulingWorkspace csrfToken="csrf-test" onOpenExecution={vi.fn()} />);
    expect((await screen.findAllByText("Monthly Revenue Forecast · Monthly")).length).toBeGreaterThan(0);
    expect(screen.getByText("Oracle Pipeline · Monthly Revenue Forecast")).toBeTruthy();
    expect(screen.getByText("Oracle defaults")).toBeTruthy();
  });

  it("live-inspects the Pipeline before creating a schedule", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/operations/pipelines/catalog") return response(pipelineCatalog);
      if (url === "/api/v1/operations/pipelines/PIPE01/preflight") return response(pipelinePreview);
      if (url === "/api/v1/schedules/preview") return response({ status: "success", next_run_at: "2027-03-01T02:30:00+00:00", next_run_local: "2027-03-01T08:00:00+05:30", message: "The live Pipeline is ready." });
      if (url === "/api/v1/schedules" && init?.method === "POST") return response({ status: "success", message: "Schedule created.", schedule }, 201);
      if (url === "/api/v1/schedules") return response({ status: "success", schedules: [schedule] });
      if (url === "/api/v1/schedules/runs/history") return response(emptyHistory);
      return response({ detail: "Unexpected request" }, 404);
    });

    render(<SchedulingWorkspace csrfToken="csrf-test" onOpenExecution={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Create schedule" }));
    fireEvent.click(screen.getByRole("button", { name: /Validate & review/i }));

    expect(await screen.findByText("Live validation passed")).toBeTruthy();
    const dialog = screen.getByRole("dialog", { name: "Configure the schedule" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create schedule" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => String(url) === "/api/v1/schedules" && init?.method === "POST")).toBe(true));
    const previewCall = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/schedules/preview");
    expect(new Headers(previewCall?.[1]?.headers).get("X-CSRF-Token")).toBe("csrf-test");
  });

  it("keeps discovered fixed Pipeline inputs visible while values are edited", async () => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/operations/pipelines/catalog") return response(pipelineCatalog);
      if (url === "/api/v1/operations/pipelines/PIPE01/preflight") return response(pipelinePreview);
      if (url === "/api/v1/schedules") return response({ status: "success", schedules: [] });
      if (url === "/api/v1/schedules/runs/history") return response(emptyHistory);
      return response({ detail: "Unexpected request" }, 404);
    });

    render(<SchedulingWorkspace csrfToken="csrf-test" onOpenExecution={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Create schedule" }));
    fireEvent.change(screen.getByLabelText("Input strategy"), { target: { value: "FIXED" } });
    fireEvent.click(screen.getByRole("button", { name: /Load Pipeline inputs/i }));

    const year = await screen.findByDisplayValue("FY27");
    fireEvent.change(year, { target: { value: "FY28" } });

    expect((screen.getByDisplayValue("FY28") as HTMLInputElement).value).toBe("FY28");
    expect(screen.getByRole("button", { name: /Validate & review/i })).toBeTruthy();
  });

  it("schedules a fully automated RTP registry synchronization", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/operations/pipelines/catalog") return response(pipelineCatalog);
      if (url === "/api/v1/schedules/preview") return response({ status: "success", next_run_at: "2027-03-01T02:30:00+00:00", next_run_local: "2027-03-01T08:00:00+05:30", message: "Oracle can generate the Calculation Manager snapshot, and the automated recurrence is ready." });
      if (url === "/api/v1/schedules") return response({ status: "success", schedules: [] });
      if (url === "/api/v1/schedules/runs/history") return response(emptyHistory);
      return response({ detail: "Unexpected request" }, 404);
    });

    render(<SchedulingWorkspace csrfToken="csrf-test" onOpenExecution={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Create schedule" }));
    fireEvent.change(screen.getByLabelText("Automation type"), { target: { value: "RTP_REGISTRY_SYNC" } });
    fireEvent.click(screen.getByRole("button", { name: /Validate & review/i }));

    expect(await screen.findByText("Live validation passed")).toBeTruthy();
    expect(screen.getByText("BISP_CalcManager_RTP")).toBeTruthy();
    const previewCall = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/schedules/preview");
    const body = JSON.parse(String(previewCall?.[1]?.body));
    expect(body.target_type).toBe("RTP_REGISTRY_SYNC");
    expect(body.target_key).toBe("BISP_CalcManager_RTP");
    expect(body.variables).toEqual({});
  });

  it("shows occurrence evidence and opens submitted execution details", async () => {
    const openExecution = vi.fn();
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/operations/pipelines/catalog") return response(pipelineCatalog);
      if (url === "/api/v1/schedules") return response({ status: "success", schedules: [schedule] });
      if (url === "/api/v1/schedules/runs/history") return response({
        status: "success",
        summary: { total: 1, submitted: 1, completed: 0, failed: 0, skipped: 0, claimed: 0 },
        runs: [{ run_id: 1, schedule_id: 11, schedule_name: schedule.name, target_type: "ORACLE_PIPELINE", target_key: "PIPE01", scheduled_for: "2027-03-01T02:30:00+00:00", claimed_at: "2027-03-01T02:30:01+00:00", completed_at: "2027-03-01T02:30:02+00:00", status: "SUBMITTED", execution_id: "execution-1", error_message: null }]
      });
      return response({ detail: "Unexpected request" }, 404);
    });

    render(<SchedulingWorkspace csrfToken="csrf-test" onOpenExecution={openExecution} />);
    fireEvent.click(await screen.findByRole("button", { name: "View execution" }));
    expect(openExecution).toHaveBeenCalledWith("execution-1");
  });
});
