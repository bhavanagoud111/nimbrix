"use client";

import type { ErrorResponse, paths } from "@nimbrix/contracts";
import createClient, { type Middleware } from "openapi-fetch";

/** Error thrown for any non-2xx API response, carrying the stable error envelope. */
export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly requestId: string | null,
    readonly details: { field?: string | null; message: string }[] = [],
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.split("; ").find((c) => c.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

export function newIdempotencyKey(): string {
  return `ui-${crypto.randomUUID()}`;
}

const securityMiddleware: Middleware = {
  onRequest({ request }) {
    if (UNSAFE.has(request.method)) {
      const csrf = readCookie("nimbrix_csrf");
      if (csrf) request.headers.set("X-CSRF-Token", csrf);
      if (!request.headers.has("Idempotency-Key")) {
        request.headers.set("Idempotency-Key", newIdempotencyKey());
      }
    }
    return request;
  },
  onResponse({ response }) {
    if (
      response.status === 401 &&
      typeof window !== "undefined" &&
      !window.location.pathname.startsWith("/login")
    ) {
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      // A full navigation is intended here: it drops all cached tenant data after a 401.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign(`/login?next=${next}`);
    }
    return response;
  },
};

export const api = createClient<paths>({ baseUrl: "" });
api.use(securityMiddleware);

/** Unwrap an openapi-fetch result: return data or throw ApiRequestError. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.response.ok && result.data !== undefined) return result.data;
  if (result.response.ok && result.response.status === 204) return undefined as T;
  const envelope = result.error as Partial<ErrorResponse> | undefined;
  const body = envelope?.error;
  throw new ApiRequestError(
    body?.message ?? `Request failed (${result.response.status})`,
    result.response.status,
    body?.code ?? "http_error",
    body?.request_id ?? result.response.headers.get("x-request-id"),
    body?.details ?? [],
  );
}
