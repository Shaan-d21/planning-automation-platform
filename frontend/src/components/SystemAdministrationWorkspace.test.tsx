import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SystemAdministrationWorkspace } from "./SystemAdministrationWorkspace";

describe("SystemAdministrationWorkspace", () => {
  it("shows concurrent use and revokes a non-current active session", () => {
    const revoke = vi.fn(async () => undefined);
    render(<SystemAdministrationWorkspace
      data={{
        status: "ready",
        summary: { active_sessions: 2, concurrent_accounts: 1, unique_active_ips: 2, failed_logins_24h: 1 },
        applications: [],
        administrators: [{ user_id: 1, username: "admin", display_name: "Admin", active: true, system_administrator: true }],
        concurrent_accounts: [{ user_id: 1, username: "admin", display_name: "Admin", session_count: 2, ip_addresses: ["192.0.2.1", "198.51.100.2"] }],
        sessions: [
          { session_key: "a".repeat(64), user_id: 1, username: "admin", display_name: "Admin", authentication_method: "local", login_ip: "192.0.2.1", current_ip: "192.0.2.1", country_code: "IN", user_agent: "Browser", cloudflare_ray: null, started_at: "2026-09-28T10:00:00Z", last_seen_at: "2026-09-28T10:00:00Z", expires_at: "2026-09-28T18:00:00Z", ended_at: null, revoked_at: null, revoke_reason: null, active: true, current: true },
          { session_key: "b".repeat(64), user_id: 1, username: "admin", display_name: "Admin", authentication_method: "oracle_basic", login_ip: "198.51.100.2", current_ip: "198.51.100.2", country_code: "US", user_agent: "Other Browser", cloudflare_ray: "ray", started_at: "2026-09-28T10:05:00Z", last_seen_at: "2026-09-28T10:05:00Z", expires_at: "2026-09-28T18:05:00Z", ended_at: null, revoked_at: null, revoke_reason: null, active: true, current: false }
        ],
        events: []
      }}
      busySessionKey={null}
      busyUserId={null}
      onRevoke={revoke}
      onAdministratorChange={vi.fn(async () => undefined)}
      busyApplication={null}
      onRegisterEnvironment={vi.fn(async () => undefined)}
      onRemoveApplication={vi.fn(async () => undefined)}
    />);

    expect(screen.getByText("Accounts needing review")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    expect(revoke).toHaveBeenCalledWith("b".repeat(64));
  });

  it("registers an environment and confirms application removal", async () => {
    const registerEnvironment = vi.fn(async () => undefined);
    const removeApplication = vi.fn(async () => undefined);
    render(<SystemAdministrationWorkspace
      data={{
        status: "ready",
        summary: { active_sessions: 1, concurrent_accounts: 0, unique_active_ips: 1, failed_logins_24h: 0 },
        applications: [
          { application_id: 1, application_name: "Vision", business_process: "PLANNING", product_type: "HP", application_type: "PBCS", environment_label: "Deployment environment", deployment_application: true, current: true, removable: false, last_verified_at: null },
          { application_id: 2, application_name: "Close", business_process: "FCCS", product_type: "HP", application_type: "FCCS", environment_label: "Oracle environment 2", deployment_application: false, current: false, removable: true, last_verified_at: "2026-10-09T10:00:00Z" }
        ],
        administrators: [],
        concurrent_accounts: [],
        sessions: [],
        events: []
      }}
      busySessionKey={null}
      onRevoke={vi.fn(async () => undefined)}
      busyUserId={null}
      onAdministratorChange={vi.fn(async () => undefined)}
      busyApplication={null}
      onRegisterEnvironment={registerEnvironment}
      onRemoveApplication={removeApplication}
    />);

    fireEvent.change(screen.getByLabelText("Oracle environment URL"), {
      target: { value: "https://close.example.oraclecloud.com/" }
    });
    fireEvent.click(screen.getByRole("button", { name: "Verify and register" }));
    expect(registerEnvironment).toHaveBeenCalledWith("https://close.example.oraclecloud.com/");

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(screen.getByText("Remove Close?")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove application" }));
    expect(removeApplication).toHaveBeenCalledWith(2);
  });
});
