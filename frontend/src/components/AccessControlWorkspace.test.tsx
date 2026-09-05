import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  AccessControlResponse,
  IdentityMappingCatalogResponse,
  IdentityProvisioningPreview
} from "../api/types";
import { AccessControlWorkspace } from "./AccessControlWorkspace";

const data: AccessControlResponse = {
  status: "success",
  current_user_id: 1,
  users: [],
  roles: [
    { code: "SERVICE_ADMINISTRATOR", name: "Service Administrator", description: "Administer the platform.", permissions: [] },
    { code: "POWER_USER", name: "Power User", description: "Run governed operations.", permissions: [] },
    { code: "USER", name: "User", description: "Perform assigned work.", permissions: [] },
    { code: "VIEWER", name: "Viewer", description: "View results.", permissions: [] }
  ],
  identity_sync: {
    available: true,
    provider_code: "oracle-cloud-epm",
    provider_name: "Oracle Cloud EPM",
    provider_registered: true,
    identity_provider_mode: "oracle_cloud",
    sso_enabled: true,
    oracle_password_login_enabled: true,
    synced_identities: 6,
    active_identities: 6,
    mapped_entitlements: 1,
    message: "Oracle access is synchronized."
  }
};

const mappings: IdentityMappingCatalogResponse = {
  status: "success",
  provider_code: "oracle-cloud-epm",
  entitlements: [{
    entitlement_id: 10,
    entitlement_type: "APPLICATION_ROLE",
    external_key: "service-administrator",
    display_name: "Service Administrator",
    active: true,
    assigned_identity_count: 6,
    mapped_role: "POWER_USER",
    mapping_enabled: true
  }]
};

const provisioning: IdentityProvisioningPreview = {
  provider_code: "oracle-cloud-epm",
  checksum: "a".repeat(64),
  generated_at: "2026-08-21T10:00:00Z",
  summary: { creates: 1, updates: 0, unchanged: 0, deactivations: 0, unmapped: 0, conflicts: 0 },
  entries: [{
    external_identity_id: 21,
    user_id: null,
    username: "planner",
    display_name: "Finance Planner",
    email: "planner@example.com",
    target_role: "POWER_USER",
    action: "CREATE",
    matched_entitlements: ["Service Administrator"],
    explanation: "Create a linked Oracle profile."
  }]
};

const noop = vi.fn(async () => undefined);

function renderWorkspace(preview: IdentityProvisioningPreview | null = null) {
  return render(<AccessControlWorkspace
    data={data}
    busyUserId={null}
    onCreate={noop}
    onUpdate={noop}
    onResetPassword={noop}
    identitySyncBusy={false}
    identitySyncPreview={null}
    onPreviewIdentitySync={noop}
    onApplyIdentitySync={noop}
    onCancelIdentitySync={vi.fn()}
    identityMappings={mappings}
    identityProvisioningPreview={preview}
    onSetIdentityMapping={noop}
    onPreviewProvisioning={noop}
    onApplyProvisioning={noop}
    onCancelProvisioning={vi.fn()}
  />);
}

describe("AccessControlWorkspace identity guidance", () => {
  it("makes the entitlement-wide mapping impact explicit", () => {
    renderWorkspace();

    expect(screen.getByText("Oracle first, local recovery second")).toBeTruthy();
    expect(screen.getByText(/All 6 will derive/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Preview linked profiles" })).toBeTruthy();
  });

  it("labels provisioning as a combined preview and shows its source entitlement", () => {
    renderWorkspace(provisioning);

    expect(screen.getByRole("heading", { name: "Review all mapped profile changes" })).toBeTruthy();
    expect(screen.getByText(/combined preview across every saved/)).toBeTruthy();
    expect(screen.getByText("Matched: Service Administrator")).toBeTruthy();
  });
});
