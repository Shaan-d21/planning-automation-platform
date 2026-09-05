import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "./client";


describe("API correlation evidence", () => {
  afterEach(() => vi.restoreAllMocks());

  it("retains the server request ID on user-visible API failures", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(
      JSON.stringify({ detail: "Oracle is temporarily unavailable." }),
      {
        status: 503,
        headers: {
          "Content-Type": "application/json",
          "X-Request-ID": "support-epm-123"
        }
      }
    ));

    const error = await api.health().catch((reason) => reason);

    expect(error).toBeInstanceOf(ApiError);
    expect(error.requestId).toBe("support-epm-123");
    expect(error.message).toContain("Reference: support-epm-123");
  });
});
