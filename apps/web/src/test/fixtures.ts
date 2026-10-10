import type { ReadinessResponse } from "@nimbrix/contracts";

export function readiness(overrides: Partial<ReadinessResponse> = {}): ReadinessResponse {
  return {
    status: "ready",
    service: "nimbrix-api",
    version: "0.1.0",
    environment: "test",
    remediation_mode: "simulation_only",
    checks: {
      database: { status: "ok", latency_ms: 1.2, detail: null },
      migrations: {
        status: "ok",
        current_revision: "0001_create_tenants",
        expected_revision: "0001_create_tenants",
      },
    },
    ...overrides,
  };
}

export function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}
