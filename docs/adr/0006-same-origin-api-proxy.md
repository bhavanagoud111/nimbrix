# ADR-0006: Browser reaches the API only through a same-origin proxy

- Status: Accepted
- Date: 2026-10-10

## Context

Next.js inlines `NEXT_PUBLIC_*` variables at build time, so a browser-side API base URL
would freeze into the image. Direct browser→API calls also require CORS, and later
cookie sessions would require cross-site cookie and CSRF handling. Operational
endpoints (`/metrics`, OpenAPI docs) should not be reachable from the public origin.

## Decision

The browser calls only its own origin. A Next.js route handler at
`/api/v1/[...path]` forwards requests server-side to `NIMBRIX_API_INTERNAL_URL`
(read at **runtime**). The proxy:

- rejects empty, `.`, `..`, or slash-containing segments and blocks `metrics`, `docs`, `openapi.json`;
- forwards only allowlisted request headers (`accept`, `content-type`, `idempotency-key`,
  `x-request-id`) and response headers (`content-type`, `x-request-id`, `cache-control`, `retry-after`);
- applies a 10 s upstream timeout and maps network failures to a `502 upstream_unavailable` envelope.

The API keeps an explicit CORS allowlist (default empty) for non-browser or future
cross-origin needs; `*` is rejected by configuration validation.

## Alternatives considered

- **`next.config` rewrites** — destinations are resolved at build time, so the API URL
  would be baked into the image. Rejected.
- **Direct browser calls + CORS** — exposes the API origin and complicates future
  cookie/CSRF handling. Rejected for the default deployment.

## Consequences

- One image works in any environment; the API URL is runtime configuration.
- Session cookies (Phase 1) will be first-party; the proxy will forward the session
  explicitly when authentication lands, and that change will be reviewed for CSRF.
