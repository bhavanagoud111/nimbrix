# Nimbrix Security

> Phase 0 baseline. A full threat model (prompt injection, tenant leakage, malicious
> CSV, IAM misuse, approval bypass, double execution, stale cloud state) is a Phase 8
> deliverable; the trust boundaries are already defined in
> [architecture.md §5](architecture.md#5-trust-boundaries).

## Principles

1. Billing records, tags, resource names, uploaded files, and LLM output are **untrusted data**.
2. Authorization and tenant scoping are enforced **on the server**; client-supplied tenant IDs are never trusted.
3. No cloud writes. Remediation is simulation-only ([ADR-0005](adr/0005-safety-policy.md)).
4. Secrets never appear in code, Git history, browser bundles, logs, prompts, or screenshots.
5. Deterministic code — not an LLM — computes money, forecasts, risk, and policy decisions.

## Controls implemented in Phase 0

| Area | Control | Verified by |
| --- | --- | --- |
| Configuration | Typed settings; invalid config fails at startup. Error messages list variable names, **never values** (the raw validation error with inputs is suppressed). | `tests/unit/test_config.py` |
| Configuration | `NIMBRIX_LIVE_ACTIONS_ENABLED=true` rejected; `*` CORS origin rejected; non-http(s) origins rejected; DEBUG logging rejected in production. | `tests/unit/test_config.py` |
| Secrets | `database_url` is a `SecretStr` (masked in repr/dumps). `.env` is git-ignored; only `.env.example` with placeholder values is committed. | `test_database_url_is_secret`, `.gitignore` |
| Logging | JSON logs; URL credentials and `password/secret/token/api_key/access_key/authorization` key-value pairs are redacted, including in tracebacks. Uvicorn access log disabled (bypasses redaction). | `tests/unit/test_logging.py` |
| Error handling | Stable envelope; validation errors omit submitted values; unexpected exceptions return a generic 500 with request ID only. | `tests/unit/test_http_foundation.py` |
| Readiness | Returns "database unreachable" instead of driver messages (which can contain hosts/usernames). | `test_not_ready_when_database_unreachable_does_not_leak_details` |
| Request IDs | Caller IDs accepted only if short and log-safe (prevents log injection); otherwise regenerated. | `test_unsafe_request_id_is_replaced` |
| Metrics | Labels use route templates (no IDs/PII in labels, bounded cardinality). | `test_metrics_use_route_templates` |
| Web proxy | Path segment validation; header allowlists both directions (cookies, `authorization`, `set-cookie` not forwarded); `metrics`, `docs`, `openapi.json` blocked; upstream timeout. | `apps/web/src/lib/api/proxy.test.ts`, `tests/e2e/status.spec.ts` |
| Browser | No API URL, secret, or provider key in client code; `poweredByHeader` disabled. | Code review; `NEXT_PUBLIC_*` not used |
| Containers | Non-root users (uid 10001); minimal slim/alpine bases; only `127.0.0.1` host port bindings; health checks. | `infrastructure/docker/*.Dockerfile`, `docker-compose.yml` |
| Database | Parameterized SQL via SQLAlchemy; per-connection `statement_timeout`; connect timeout. | `db/session.py` |
| CI | Least-privilege `permissions: contents: read`; throwaway CI-only DB password. | `.github/workflows/ci.yml` |

## Known gaps (tracked, not hidden)

- **No authentication or RBAC yet** — Phase 1. The only endpoints are health/metrics.
- `/api/v1/metrics` and `/api/v1/docs` are unauthenticated on the API container port;
  they are bound to `127.0.0.1` locally and blocked at the web proxy. Production network
  policy is a Phase 8/9 item.
- No rate limiting, CSP, or security headers beyond defaults yet — Phase 8.
- `npm audit` reports 5 high-severity advisories, all in the **dev-only** lint toolchain
  (`braces` via `micromatch` → `fast-glob` → `@next/eslint-plugin-next`). No fix exists
  without downgrading `eslint-config-next` to 14.x. Not shipped in the runtime image.
- Secret scanning and dependency review in CI — Phase 8.

## Reporting

This is a portfolio project. Report security issues privately to the repository owner
rather than opening a public issue.
