/**
 * Backend-for-frontend proxy: the browser only ever talks to the web origin,
 * and this server-side code forwards `/api/v1/*` to the internal API URL.
 * No API base URL, secret, or credential is shipped in the browser bundle.
 */

const FORWARDED_REQUEST_HEADERS = [
  "accept",
  "content-type",
  "idempotency-key",
  "x-request-id",
  "x-csrf-token",
  "user-agent",
];
const FORWARDED_RESPONSE_HEADERS = [
  "content-type",
  "content-disposition",
  "x-request-id",
  "cache-control",
  "retry-after",
  "idempotent-replayed",
];
/** Only Nimbrix cookies cross the proxy, in either direction. */
const COOKIE_PREFIX = "nimbrix_";

export function filterCookieHeader(header: string | null): string | null {
  if (!header) return null;
  const kept = header
    .split(";")
    .map((part) => part.trim())
    .filter((part) => part.startsWith(COOKIE_PREFIX));
  return kept.length > 0 ? kept.join("; ") : null;
}
/** Operational endpoints that must not be reachable through the public web origin. */
const BLOCKED_PATHS = new Set(["metrics", "openapi.json", "docs"]);
const UPSTREAM_TIMEOUT_MS = 10_000;

export class ProxyConfigError extends Error {}

export function apiBaseUrl(env: Record<string, string | undefined> = process.env): URL {
  const raw = env.NIMBRIX_API_INTERNAL_URL;
  if (!raw) {
    throw new ProxyConfigError("NIMBRIX_API_INTERNAL_URL is not set");
  }
  const url = new URL(raw);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new ProxyConfigError("NIMBRIX_API_INTERNAL_URL must be an http(s) URL");
  }
  return url;
}

function errorEnvelope(status: number, code: string, message: string, requestId: string | null) {
  return Response.json(
    { error: { code, message, request_id: requestId, details: [] } },
    { status, headers: requestId ? { "x-request-id": requestId } : undefined },
  );
}

/** Returns the upstream path, or null when the segments are unsafe or blocked. */
export function buildUpstreamPath(segments: string[]): string | null {
  if (segments.length === 0) return null;
  for (const segment of segments) {
    if (segment === "" || segment === "." || segment === ".." || segment.includes("/")) {
      return null;
    }
  }
  if (BLOCKED_PATHS.has(segments.join("/"))) return null;
  return "/api/v1/" + segments.map(encodeURIComponent).join("/");
}

export async function proxyToApi(
  request: Request,
  segments: string[],
  options: { fetchImpl?: typeof fetch; env?: Record<string, string | undefined> } = {},
): Promise<Response> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const requestId = request.headers.get("x-request-id");

  const upstreamPath = buildUpstreamPath(segments);
  if (upstreamPath === null) {
    return errorEnvelope(404, "not_found", "Not Found", requestId);
  }

  let base: URL;
  try {
    base = apiBaseUrl(options.env);
  } catch (err) {
    console.error("nimbrix_proxy_misconfigured", (err as Error).message);
    return errorEnvelope(500, "proxy_misconfigured", "API proxy is not configured", requestId);
  }

  const target = new URL(upstreamPath, base);
  target.search = new URL(request.url).search;

  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  const cookie = filterCookieHeader(request.headers.get("cookie"));
  if (cookie) headers.set("cookie", cookie);

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  let upstream: Response;
  try {
    upstream = await fetchImpl(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch (err) {
    console.error("nimbrix_proxy_upstream_error", (err as Error).name);
    return errorEnvelope(502, "upstream_unavailable", "The Nimbrix API is unreachable", requestId);
  }

  const responseHeaders = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value !== null) responseHeaders.set(name, value);
  }
  for (const setCookie of upstream.headers.getSetCookie()) {
    if (setCookie.startsWith(COOKIE_PREFIX)) responseHeaders.append("set-cookie", setCookie);
  }
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}
