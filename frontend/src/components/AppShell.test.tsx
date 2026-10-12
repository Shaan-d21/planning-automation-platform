import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { BootstrapResponse } from "../api/types";
import { AppShell } from "./AppShell";


const bootstrap: BootstrapResponse = {
  product: {
    name: "EPM AI Assistant",
    company: "BISP Solutions",
    api_version: "v1"
  },
  authenticated: true,
  requires_bootstrap: false,
  csrf_token: "csrf-test",
  identity_authentication: {
    federated_enabled: false,
    oracle_credentials_enabled: false,
    provider_name: "Oracle Cloud Identity",
    login_url: null,
    local_recovery_enabled: true
  },
  environment: {
    application_name: "Vision",
    deployment_mode: "on_premises",
    configured: true,
    business_process: "PLANNING"
  },
  user: {
    user_id: 1,
    username: "admin",
    display_name: "Test Administrator",
    email: null,
    platform_roles: ["SERVICE_ADMINISTRATOR"],
    permissions: [],
    persona: "SERVICE_ADMINISTRATOR",
    persona_label: "Service Administrator"
  },
  navigation: [
    { code: "tasks", label: "My Work", path: "#tasks", group: "Workspace" }
  ],
  workspaces: [
    {
      application_id: 1,
      application_name: "Vision",
      business_process: "PLANNING",
      product_type: "HP",
      application_type: "PBCS",
      current: true,
      switchable: true,
      unavailable_reason: null
    },
    {
      application_id: 2,
      application_name: "Close",
      business_process: "FCCS",
      product_type: "HP",
      application_type: "FCCS",
      current: false,
      switchable: true,
      unavailable_reason: null
    }
  ],
  workspace_switching_enabled: true,
  features: {
    legacy_ui: false,
    task_engine: true,
    planning_cycles: true,
    approvals: true,
    notifications: true,
    access_control: true,
    jobs_activity: true
  }
};


describe("AppShell application workspaces", () => {
  it("sends the selected registered application to the session switcher", () => {
    const onWorkspaceChange = vi.fn().mockResolvedValue(undefined);
    render(
      <AppShell
        bootstrap={bootstrap}
        activeView="tasks"
        busy={false}
        unreadNotifications={0}
        onLogout={vi.fn().mockResolvedValue(undefined)}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
        onWorkspaceChange={onWorkspaceChange}
      >
        <div>Workspace body</div>
      </AppShell>
    );

    fireEvent.change(screen.getByLabelText("Oracle application"), {
      target: { value: "2" }
    });

    expect(onWorkspaceChange).toHaveBeenCalledWith(2);
  });
});
