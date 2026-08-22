import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AgentExecutionCard } from "./EpmAssistantWorkspace";

function response(value: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" }
  }));
}

describe("standalone flow recovery", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("reviews files and explicitly starts a linked retry", async () => {
    const started = vi.fn();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/operations/runs/failed-flow") return response({
        execution_id: "failed-flow",
        operation_name: "Standalone Flow - Monthly Forecast",
        status: "FAILED",
        started_at: "2026-08-20T10:00:00Z",
        completed_at: "2026-08-20T10:02:00Z",
        error_message: "Import failed at row 18.",
        initiated_by: "Planning Administrator",
        trigger_source: "AI_AGENT",
        steps: [],
        completed_steps: 3,
        total_steps: 3,
        artifacts: [],
        record_statistics: null,
        flow_progress: {
          current_step: null,
          completed_steps: 3,
          successful_steps: 1,
          total_steps: 3,
          progress_percent: 100,
          record_statistics: null,
          recovery: null,
          steps: [
            { sequence: 1, operation_code: "business-rules", display_name: "Business Rules", artifact_name: "Calculate Forecast", status: "SUCCESS", started_at: null, completed_at: null, child_execution_id: "failed-flow-step-1", child_status: "SUCCESS", active_stage: null, oracle_job_id: 10, oracle_status: "Completed", record_statistics: null, error_message: null },
            { sequence: 2, operation_code: "data-import", display_name: "Planning Data Import", artifact_name: "Import Forecast", status: "FAILED", started_at: null, completed_at: null, child_execution_id: "failed-flow-step-2", child_status: "FAILED", active_stage: null, oracle_job_id: 11, oracle_status: "Failed", record_statistics: null, error_message: "Import failed at row 18." },
            { sequence: 3, operation_code: "data-maps", display_name: "Data Maps", artifact_name: "Forecast to Reporting", status: "SKIPPED", started_at: null, completed_at: null, child_execution_id: "failed-flow-step-3", child_status: null, active_stage: null, oracle_job_id: null, oracle_status: null, record_statistics: null, error_message: null }
          ]
        },
        log_url: null,
        terminal: true
      });
      if (url === "/api/v1/operations/runs/failed-flow/recovery" && init?.method !== "POST") return response({
        status: "success",
        recovery: {
          source_execution_id: "failed-flow",
          flow_name: "Monthly Forecast",
          failed_step_sequence: 2,
          failure_reason: "Import failed at row 18.",
          retryable: true,
          blocked_reason: null,
          steps: [
            { sequence: 2, operation_code: "data-import", display_name: "Planning Data Import", artifact_name: "Import Forecast", original_status: "FAILED" },
            { sequence: 3, operation_code: "data-maps", display_name: "Data Maps", artifact_name: "Forecast to Reporting", original_status: "SKIPPED" }
          ],
          required_uploads: [
            { key: "step_2:source_file", step_sequence: 2, label: "Planning Data Import source file", original_filename: "Forecast.csv", allowed_extensions: [".csv", ".txt", ".zip"] }
          ]
        }
      });
      if (url === "/api/v1/uploads?filename=Forecast.csv" && init?.method === "POST") return response({ status: "success", upload: { token: "replacement-token", filename: "Forecast.csv", size: 20 } });
      if (url === "/api/v1/operations/runs/failed-flow/recovery" && init?.method === "POST") return response({ status: "accepted", execution_id: "recovery-flow", source_execution_id: "failed-flow", redirect: "/app/operations/runs/recovery-flow" }, 202);
      return response({ detail: `Unexpected request: ${url}` }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AgentExecutionCard
      approved={{ execution_id: "failed-flow", operation_code: "standalone-flow", target_name: "Monthly Forecast", status: "QUEUED" }}
      csrfToken="csrf-test"
      onRecoveryStarted={started}
      onDismiss={() => undefined}
    />);

    fireEvent.click(await screen.findByRole("button", { name: /Review recovery/ }));
    expect(await screen.findByRole("heading", { name: "Retry from failed step" })).toBeTruthy();
    expect(screen.getByText("Completed steps before step 2 will not run again.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/Planning Data Import source file/), {
      target: { files: [new File(["Account,Value\nSales,100"], "Forecast.csv", { type: "text/csv" })] }
    });
    fireEvent.click(screen.getByRole("button", { name: "Approve and retry failed steps" }));

    await waitFor(() => expect(started).toHaveBeenCalledWith(expect.objectContaining({ execution_id: "recovery-flow", operation_code: "standalone-flow" })));
    const retry = fetchMock.mock.calls.find(([url, init]) => String(url) === "/api/v1/operations/runs/failed-flow/recovery" && init?.method === "POST");
    expect(JSON.parse(String(retry?.[1]?.body))).toEqual({ failed_step_sequence: 2, confirmation: "RETRY_FROM_FAILED_STEP", replacement_uploads: { "step_2:source_file": "replacement-token" } });
  });
});
