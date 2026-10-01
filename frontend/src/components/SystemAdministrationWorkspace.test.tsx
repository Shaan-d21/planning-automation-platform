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
    />);

    expect(screen.getByText("Accounts needing review")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    expect(revoke).toHaveBeenCalledWith("b".repeat(64));
  });
});
