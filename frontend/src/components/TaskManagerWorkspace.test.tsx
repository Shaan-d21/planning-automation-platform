import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { TaskManagerSnapshotResponse } from "../api/types";
import { TaskManagerWorkspace } from "./TaskManagerWorkspace";

const snapshot: TaskManagerSnapshotResponse = {
  status: "success",
  can_sync: true,
  configuration: {
    report_group: "Task Manager Reports",
    report_name: "All Tasks",
    parameters: { "Schedule Name": "FY27 Close" }
  },
  sync: {
    last_synced_at: "2026-10-05T10:00:00Z",
    last_sync_status: "SUCCESS",
    last_sync_record_count: 2,
    last_error: null
  },
  summary: {
    task_count: 2,
    schedule_count: 1,
    status_counts: { "In Progress": 1, Completed: 1 }
  },
  tasks: [
    {
      source_key: "one",
      external_id: "101",
      name: "Load Actuals",
      schedule_name: "FY27 Close",
      period_name: "Sep",
      status: "In Progress",
      owner: "Finance",
      assignee: "Planner",
      approver: "Controller",
      organization: "Corporate",
      task_type: "Data Load",
      priority: "High",
      description: "Load September actuals.",
      parent_task: null,
      dependency: null,
      start_at: "2026-10-05T09:00:00Z",
      due_at: "2026-10-05T17:00:00Z",
      completed_at: null,
      attributes: {}
    },
    {
      source_key: "two",
      external_id: "102",
      name: "Review Results",
      schedule_name: "FY27 Close",
      period_name: "Sep",
      status: "Completed",
      owner: "Finance",
      assignee: "Controller",
      approver: null,
      organization: "Corporate",
      task_type: "Review",
      priority: "Normal",
      description: null,
      parent_task: null,
      dependency: "Load Actuals",
      start_at: null,
      due_at: null,
      completed_at: "2026-10-05T12:00:00Z",
      attributes: {}
    }
  ]
};

describe("TaskManagerWorkspace", () => {
  it("renders synchronized schedules separately from Planning Cycles", () => {
    render(
      <TaskManagerWorkspace
        data={snapshot}
        busy={false}
        onSynchronize={vi.fn()}
      />
    );

    expect(screen.getByRole("heading", { name: "Task Manager", level: 1 })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "FY27 Close", level: 3 })).toBeTruthy();
    expect(screen.getAllByText("Load Actuals").length).toBeGreaterThan(0);
    expect(screen.getByText("No source file retained")).toBeTruthy();
    expect(screen.queryByText("Planning cycle creation")).toBeNull();
  });

  it("submits exact Oracle report configuration and parameters", async () => {
    const onSynchronize = vi.fn().mockResolvedValue(undefined);
    render(
      <TaskManagerWorkspace
        data={snapshot}
        busy={false}
        onSynchronize={onSynchronize}
      />
    );

    fireEvent.change(screen.getByDisplayValue("FY27 Close"), {
      target: { value: "October Close" }
    });
    fireEvent.click(screen.getByRole("button", { name: "Synchronize from Oracle" }));

    await waitFor(() => expect(onSynchronize).toHaveBeenCalledWith({
      report_group: "Task Manager Reports",
      report_name: "All Tasks",
      parameters: { "Schedule Name": "October Close" }
    }));
  });
});
