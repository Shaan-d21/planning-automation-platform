import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SchedulingWorkspace } from "./SchedulingWorkspace";

const catalog = {
  status: "success",
  processes: [{
    code: "MONTHLY_FORECAST",
    name: "Monthly Revenue Forecast",
    context_mode: "PIPELINE_DEFAULTS",
    supports_pipeline_defaults: true,
    presets: [{
      preset_id: 4,
      name: "FY27 March",
      one_click_ready: true,
      year: "FY27",
      start_period: "Mar",
      end_period: "Mar",
      inbox_files: [["DataFile", "March_Revenue.csv"]],
      required_upload_keys: []
    }]
  }]
};

const schedule = {
  schedule_id: 11,
  name: "Monthly Revenue Forecast · Monthly",
  process_code: "MONTHLY_FORECAST",
  frequency: "MONTHLY",
  frequency_label: "Monthly",
  timezone: "Asia/Kolkata",
  first_run_local: "2027-03-01T08:00:00",
  context_mode: "PIPELINE_DEFAULTS",
  context_label: "Oracle Pipeline defaults",
  preset_id: null,
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
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  }));
}

describe("SchedulingWorkspace", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/schedules/catalog") return response(catalog);
      if (url === "/api/v1/schedules") return response({ status: "success", schedules: [schedule] });
      if (url === "/api/v1/jobs") return response({ status: "success", summary: { total: 0, running: 0, successful: 0, failed: 0, success_rate: 0 }, jobs: [] });
      return response({ detail: "Unexpected request" }, 404);
    }));
  });

  it("shows active schedules and their next occurrence", async () => {
    render(<SchedulingWorkspace csrfToken="csrf-test" onOpenExecution={vi.fn()} />);

    expect((await screen.findAllByText("Monthly Revenue Forecast · Monthly")).length).toBeGreaterThan(0);
    expect(screen.getByText("Oracle Pipeline defaults")).toBeTruthy();
    expect(screen.getByText("No automatic occurrence has run yet")).toBeTruthy();
  });

  it("validates a guided schedule before saving it", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/schedules/catalog") return response(catalog);
      if (url === "/api/v1/schedules" && init?.method === "POST") return response({ status: "success", message: "Schedule created.", schedule }, 201);
      if (url === "/api/v1/schedules") return response({ status: "success", schedules: [schedule] });
      if (url === "/api/v1/jobs") return response({ status: "success", summary: { total: 0, running: 0, successful: 0, failed: 0, success_rate: 0 }, jobs: [] });
      if (url === "/api/v1/schedules/preview") return response({ status: "success", next_run_at: "2027-03-01T02:30:00+00:00", next_run_local: "2027-03-01T08:00:00+05:30", message: "The Process and recurrence are ready." });
      return response({ detail: "Unexpected request" }, 404);
    });
    render(<SchedulingWorkspace csrfToken="csrf-test" onOpenExecution={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "Create schedule" }));
    expect(screen.getByRole("dialog", { name: "Configure the schedule" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Review schedule/i }));

    expect(await screen.findByText("Live validation passed")).toBeTruthy();
    const reviewDialog = screen.getByRole("dialog", { name: "Review before saving" });
    fireEvent.click(within(reviewDialog).getByRole("button", { name: "Create schedule" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) =>
      String(url) === "/api/v1/schedules/preview" && init?.method === "POST"
    )).toBe(true));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) =>
      String(url) === "/api/v1/schedules" && init?.method === "POST"
    )).toBe(true));
    const previewCall = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/schedules/preview");
    expect(new Headers(previewCall?.[1]?.headers).get("X-CSRF-Token")).toBe("csrf-test");
  });
});
