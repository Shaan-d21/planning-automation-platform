import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { FCCSReadWorkspace } from "./FCCSReadWorkspace";

describe("FCCSReadWorkspace", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("renders a verified read-only FCCS overview", async () => {
    vi.spyOn(api, "fccsOverview").mockResolvedValue({
      status: "success",
      application_name: "Consolidation",
      product_type: "HP",
      application_type: "FCCS",
      connected: true,
      plan_types: [{
        name: "Consol",
        cube_name: "Consol",
        identifier: 1,
        cube_type: 0,
        dimension_count: 2,
        dimensions: [
          { name: "Account", dimension_type: "Account" },
          { name: "Entity", dimension_type: "Entity" }
        ]
      }],
      job_definitions: [{ job_name: "Consolidate", job_type: "RULES" }]
    });

    render(<FCCSReadWorkspace view="fccs-overview" />);

    expect(await screen.findByRole("heading", { name: "Consolidation" })).toBeTruthy();
    expect(screen.getByText("Read-only foundation")).toBeTruthy();
    expect(screen.getByText("Disabled in this release phase")).toBeTruthy();
  });

  it("passes only explicit journal filters and opens read-only detail", async () => {
    vi.spyOn(api, "fccsJournals").mockResolvedValue({
      status: "success",
      offset: 0,
      limit: 50,
      journals: [{
        label: "J1",
        scenario: "Actual",
        year: "FY27",
        period: "Jan",
        status: "Working",
        consolidation: "FCCS_Entity Input",
        description: "Close adjustment",
        group: null,
        journal_type: null,
        balance_type: null,
        created_by: null,
        modified_by: null,
        posted_by: null
      }]
    });
    vi.spyOn(api, "fccsJournalDetail").mockResolvedValue({
      status: "success",
      journal: {
        label: "J1",
        scenario: "Actual",
        year: "FY27",
        period: "Jan",
        status: "Working",
        consolidation: "FCCS_Entity Input",
        description: "Close adjustment",
        group: null,
        journal_type: null,
        balance_type: null,
        created_by: null,
        modified_by: null,
        posted_by: null
      },
      line_items: [{ account: "Cash", amount: 100 }]
    });

    render(<FCCSReadWorkspace view="fccs-journals" />);
    expect(await screen.findByText("J1")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Scenario"), { target: { value: "Actual" } });
    fireEvent.click(screen.getByRole("button", { name: "Search journals" }));
    await waitFor(() => expect(api.fccsJournals).toHaveBeenLastCalledWith({
      label: "",
      scenario: "Actual",
      year: "",
      period: "",
      status: "",
      consolidation: ""
    }));
    fireEvent.click(screen.getByRole("button", { name: "View details" }));
    expect(await screen.findByRole("dialog", { name: "J1" })).toBeTruthy();
    expect(screen.getByText("Cash")).toBeTruthy();
  });
});
