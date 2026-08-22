import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConfirmationDialog, FeedbackBanner } from "./Feedback";

describe("shared feedback components", () => {
  it("announces errors and allows them to be dismissed", () => {
    const dismiss = vi.fn();
    render(<FeedbackBanner tone="error" title="Action required" message="Oracle rejected the request." onDismiss={dismiss} />);

    expect(screen.getByRole("alert").textContent).toContain("Oracle rejected the request.");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss message" }));
    expect(dismiss).toHaveBeenCalledOnce();
  });

  it("requires an explicit dialog action before destructive work", () => {
    const confirm = vi.fn();
    render(<ConfirmationDialog title="Delete conversation?" description="Remove this conversation." warning="This cannot be undone." confirmLabel="Delete conversation" tone="danger" onConfirm={confirm} onClose={() => undefined} />);

    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(confirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Delete conversation" }));
    expect(confirm).toHaveBeenCalledOnce();
  });
});
