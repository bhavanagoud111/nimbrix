import { describe, expect, it, vi } from "vitest";
import { jsonResponse, readiness } from "@/test/fixtures";
import { ApiUnavailableError, fetchReadiness } from "./health";

describe("fetchReadiness", () => {
  it("returns readiness on 200", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(readiness()));
    await expect(fetchReadiness(fetchImpl)).resolves.toMatchObject({ status: "ready" });
    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/health/ready", expect.anything());
  });

  it("treats a 503 readiness body as data", async () => {
    const body = readiness({ status: "not_ready" });
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body, 503));
    await expect(fetchReadiness(fetchImpl)).resolves.toMatchObject({ status: "not_ready" });
  });

  it("throws with the envelope message and request id on proxy errors", async () => {
    const envelope = {
      error: {
        code: "upstream_unavailable",
        message: "The Nimbrix API is unreachable",
        request_id: "r-1",
        details: [],
      },
    };
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse(envelope, 502, { "x-request-id": "req-12345678" }));
    const error = await fetchReadiness(fetchImpl).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiUnavailableError);
    expect(error).toMatchObject({
      message: "The Nimbrix API is unreachable",
      status: 502,
      requestId: "req-12345678",
    });
  });

  it("throws on network failure", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(fetchReadiness(fetchImpl)).rejects.toThrow(
      "Could not reach the Nimbrix web server",
    );
  });

  it("rejects malformed bodies", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("<html>", { status: 200 }));
    await expect(fetchReadiness(fetchImpl)).rejects.toThrow("Unexpected response (200)");
  });
});
