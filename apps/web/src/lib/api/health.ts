import type { ErrorResponse, ReadinessResponse } from "@nimbrix/contracts";

export class ApiUnavailableError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly requestId?: string | null,
  ) {
    super(message);
    this.name = "ApiUnavailableError";
  }
}

function isReadiness(body: unknown): body is ReadinessResponse {
  return (
    typeof body === "object" &&
    body !== null &&
    "status" in body &&
    "checks" in body &&
    (body.status === "ready" || body.status === "not_ready")
  );
}

/**
 * Fetch API readiness through the same-origin proxy. A 503 carries a valid
 * readiness body (dependency down), so it is returned as data, not thrown.
 */
export async function fetchReadiness(fetchImpl: typeof fetch = fetch): Promise<ReadinessResponse> {
  let response: Response;
  try {
    response = await fetchImpl("/api/v1/health/ready", {
      headers: { accept: "application/json" },
      cache: "no-store",
    });
  } catch {
    throw new ApiUnavailableError("Could not reach the Nimbrix web server");
  }

  const requestId = response.headers.get("x-request-id");
  const body: unknown = await response.json().catch(() => null);
  if ((response.ok || response.status === 503) && isReadiness(body)) {
    return body;
  }
  const message =
    (body as ErrorResponse | null)?.error?.message ?? `Unexpected response (${response.status})`;
  throw new ApiUnavailableError(message, response.status, requestId);
}
