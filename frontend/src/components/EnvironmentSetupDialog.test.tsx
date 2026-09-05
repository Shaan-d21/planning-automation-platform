import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { EnvironmentConfigurationResponse } from "../api/types";
import { EnvironmentSetupDialog } from "./EnvironmentSetupDialog";

const configuration: EnvironmentConfigurationResponse = {
  status: "success",
  base_url: "https://example.oraclecloud.com",
  deployment_mode: "cloud",
  active_application: "Vision",
  selected_application: "Vision",
  selection_source: "ENVIRONMENT",
  configured: true,
  restart_required: false,
  applications: [
    { name: "Forecast", product_type: "HP", application_type: null, admin_mode: false },
    { name: "Vision", product_type: "HP", application_type: null, admin_mode: false }
  ],
  last_discovered_at: "2026-08-27T10:00:00Z",
  last_discovery_error: null,
  message: "Application 'Vision' is configured."
};

describe("EnvironmentSetupDialog", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/environment/configuration") {
        return response(configuration);
      }
      if (url === "/api/v1/environment/application" && init?.method === "PUT") {
        return response({
          ...configuration,
          selected_application: "Forecast",
          selection_source: "ADMIN_SELECTION",
          restart_required: true,
          message: "Application 'Forecast' was saved. Restart the API and worker services once."
        });
      }
      return response({ detail: "Unexpected request" }, 404);
    }));
  });

  it("shows live applications and explains a controlled restart", async () => {
    render(<EnvironmentSetupDialog csrfToken="csrf-token" onClose={vi.fn()} />);

    fireEvent.click(await screen.findByRole("radio", { name: /Forecast/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save application" }));

    expect(await screen.findByText("Restart required before running operations")).toBeTruthy();
    expect(screen.getByText(/Restart the API and worker services once/)).toBeTruthy();
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/v1/environment/application",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ application_name: "Forecast" })
      })
    ));
  });
});

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}
