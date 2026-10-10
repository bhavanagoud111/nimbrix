// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import {
  apiBaseUrl,
  buildUpstreamPath,
  filterCookieHeader,
  ProxyConfigError,
  proxyToApi,
} from "./proxy";

const env = { NIMBRIX_API_INTERNAL_URL: "http://api.internal:8000" };

describe("buildUpstreamPath", () => {
  it("maps segments under /api/v1", () => {
    expect(buildUpstreamPath(["health", "ready"])).toBe("/api/v1/health/ready");
  });

  it.each([[[]], [[".."]], [["health", "."]], [["a/b"]], [["", "x"]]])(
    "rejects unsafe segments %j",
    (segments) => {
      expect(buildUpstreamPath(segments)).toBeNull();
    },
  );

  it.each([["metrics"], ["docs"], ["openapi.json"]])("blocks operational path %s", (path) => {
    expect(buildUpstreamPath([path])).toBeNull();
  });

  it("percent-encodes segment content", () => {
    expect(buildUpstreamPath(["resources", "i-123 ?x"])).toBe("/api/v1/resources/i-123%20%3Fx");
  });
});

describe("filterCookieHeader", () => {
  it("keeps only nimbrix cookies", () => {
    expect(filterCookieHeader("a=1; nimbrix_session=x")).toBe("nimbrix_session=x");
    expect(filterCookieHeader("a=1")).toBeNull();
    expect(filterCookieHeader(null)).toBeNull();
  });
});

describe("apiBaseUrl", () => {
  it("requires configuration", () => {
    expect(() => apiBaseUrl({})).toThrow(ProxyConfigError);
  });

  it("rejects non-http schemes", () => {
    expect(() => apiBaseUrl({ NIMBRIX_API_INTERNAL_URL: "file:///etc/passwd" })).toThrow(
      ProxyConfigError,
    );
  });
});

describe("proxyToApi", () => {
  it("forwards allowlisted headers and query strings, and relays the response", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response('{"status":"ok"}', {
        status: 200,
        headers: {
          "content-type": "application/json",
          "x-request-id": "req-12345678",
          "set-cookie": "leak=1",
        },
      }),
    );
    const request = new Request("http://web.local/api/v1/health/live?verbose=1", {
      headers: {
        "x-request-id": "req-12345678",
        cookie: "other_app=secret; nimbrix_session=abc; nimbrix_csrf=def",
        authorization: "Bearer should-not-forward",
        "x-csrf-token": "def",
      },
    });

    const response = await proxyToApi(request, ["health", "live"], { fetchImpl, env });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "ok" });
    expect(response.headers.get("x-request-id")).toBe("req-12345678");
    expect(response.headers.get("set-cookie")).toBeNull();

    const [url, init] = fetchImpl.mock.calls[0];
    expect(String(url)).toBe("http://api.internal:8000/api/v1/health/live?verbose=1");
    const sent = new Headers(init?.headers);
    expect(sent.get("x-request-id")).toBe("req-12345678");
    expect(sent.get("cookie")).toBe("nimbrix_session=abc; nimbrix_csrf=def");
    expect(sent.get("x-csrf-token")).toBe("def");
    expect(sent.get("authorization")).toBeNull();
  });

  it("relays only nimbrix set-cookie headers", async () => {
    const upstreamHeaders = new Headers();
    upstreamHeaders.append("set-cookie", "nimbrix_session=t; HttpOnly; Path=/");
    upstreamHeaders.append("set-cookie", "tracker=1; Path=/");
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("{}", { status: 200, headers: upstreamHeaders }));
    const response = await proxyToApi(
      new Request("http://web.local/api/v1/auth/login", { method: "POST", body: "{}" }),
      ["auth", "login"],
      { fetchImpl, env },
    );
    expect(response.headers.getSetCookie()).toEqual(["nimbrix_session=t; HttpOnly; Path=/"]);
  });

  it("passes request bodies for mutations", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 202 }));
    const request = new Request("http://web.local/api/v1/imports/demo", {
      method: "POST",
      headers: { "content-type": "application/json", "idempotency-key": "abc" },
      body: '{"x":1}',
    });

    const response = await proxyToApi(request, ["imports", "demo"], { fetchImpl, env });

    expect(response.status).toBe(202);
    const init = fetchImpl.mock.calls[0][1];
    expect(init?.method).toBe("POST");
    expect(new TextDecoder().decode(init?.body as ArrayBuffer)).toBe('{"x":1}');
    expect(new Headers(init?.headers).get("idempotency-key")).toBe("abc");
  });

  it("returns a 502 envelope when the API is unreachable", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("fetch failed"));
    const request = new Request("http://web.local/api/v1/health/ready", {
      headers: { "x-request-id": "req-abcdefgh" },
    });

    const response = await proxyToApi(request, ["health", "ready"], { fetchImpl, env });

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({
      error: {
        code: "upstream_unavailable",
        message: "The Nimbrix API is unreachable",
        request_id: "req-abcdefgh",
        details: [],
      },
    });
  });

  it("returns 404 for blocked paths without calling the API", async () => {
    const fetchImpl = vi.fn<typeof fetch>();
    const response = await proxyToApi(new Request("http://web.local/api/v1/metrics"), ["metrics"], {
      fetchImpl,
      env,
    });
    expect(response.status).toBe(404);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("returns 500 when the proxy is misconfigured", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const response = await proxyToApi(
      new Request("http://web.local/api/v1/health/live"),
      ["health", "live"],
      {
        fetchImpl: vi.fn<typeof fetch>(),
        env: {},
      },
    );
    expect(response.status).toBe(500);
    expect((await response.json()).error.code).toBe("proxy_misconfigured");
  });
});
