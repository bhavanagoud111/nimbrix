import { describe, expect, it } from "vitest";
import { ApiRequestError, unwrap } from "./client";

describe("unwrap", () => {
  it("returns data for successful responses", () => {
    expect(unwrap({ data: { ok: true }, response: new Response(null, { status: 200 }) })).toEqual({
      ok: true,
    });
  });

  it("raises the error envelope with request id and details", () => {
    const error = {
      error: {
        code: "policy_denied",
        message: "The request violates remediation policy",
        request_id: "req-12345678",
        details: [{ field: "environment_nonprod", message: "environment tag is prod" }],
      },
    };
    try {
      unwrap({ error, response: new Response(null, { status: 422 }) });
      expect.unreachable();
    } catch (e) {
      expect(e).toBeInstanceOf(ApiRequestError);
      expect(e).toMatchObject({ status: 422, code: "policy_denied", requestId: "req-12345678" });
      expect((e as ApiRequestError).details[0].field).toBe("environment_nonprod");
    }
  });

  it("falls back to the response request id when the body is not an envelope", () => {
    const response = new Response(null, {
      status: 502,
      headers: { "x-request-id": "req-abcdefgh" },
    });
    expect(() => unwrap({ error: "<html>", response })).toThrow(ApiRequestError);
    try {
      unwrap({ error: undefined, response });
    } catch (e) {
      expect((e as ApiRequestError).requestId).toBe("req-abcdefgh");
      expect((e as ApiRequestError).message).toBe("Request failed (502)");
    }
  });
});
