# Engineering guide for contributors and coding agents

Read this before changing anything. The product and phase plan live in
`docs/architecture.md`, `docs/adr/`, and `docs/PROGRESS.md`.

## Ground rules

1. **Work in phase order.** Check `docs/PROGRESS.md` for the current phase. Do not start
   the next phase without explicit approval from the project owner.
2. **No fake functionality.** Never present mock data as live, estimated savings as
   realized, or unexecuted tests as passing. If something is blocked, mark it incomplete
   with the exact reason.
3. **No cloud writes, paid resources, credential creation, publishing, pushing, or
   destructive commands** without explicit authorization. Live remediation stays disabled.
4. **Untrusted inputs:** billing rows, tags, resource names, uploaded files, and LLM output.
   Validate them; enforce tenant scope and authorization on the server.
5. **Secrets** never go in code, Git, logs, prompts, the browser bundle, or screenshots.
6. **Deterministic math.** Money, forecasts, risk, and policy decisions are computed in
   code. LLMs explain; they are never the authority.
7. Keep it simple: modular monolith + worker; no Kafka/Kubernetes/Celery without an ADR.

## Layout

| Path | What |
| --- | --- |
| `apps/api/src/nimbrix_api/` | FastAPI app + worker. `core/` (config, auth deps, errors, logging, metrics, telemetry, protection), `db/`, `models/`, `api/v1/` (routers), `modules/` (domain services: ingestion, costs, budgets, recommendations, ml, agents, remediation, jobs), `migrations/` |
| `apps/api/tests/unit` | Fast tests, no database |
| `apps/api/tests/integration` | Real PostgreSQL; each session creates and drops its own database |
| `apps/web/src/` | Next.js 16 App Router. `app/` routes, `components/`, `lib/api/` (proxy + clients) |
| `packages/contracts/` | `openapi.json` + generated `src/schema.d.ts` — **generated, do not edit by hand** |
| `tests/e2e/` | Playwright against the running demo stack |
| `tests/performance/` | Load test for cost queries |
| `infrastructure/docker/` | Dockerfiles (build context = repo root) |
| `infrastructure/observability/` | Prometheus + Grafana (compose profile `observability`) |
| `infrastructure/terraform/aws/` | AWS deployment (validate only; never apply without approval) |
| `scripts/` | demo readiness wait, reference-doc generator, benchmarks |

## Commands

```bash
make install            # uv sync + npm ci
make up                 # full stack via Docker Compose (web :3200, api :8200, pg :5440)
make demo               # same, plus opt-in demo seed (users + synthetic data)
make check              # lint + typecheck + unit/integration tests + web build
make test-integration   # needs `make db` (or `make up`) first
make e2e                # needs `make demo` first
make contracts          # after any API schema change; commit the result
cd apps/api && uv run python ../../scripts/generate_reference_docs.py   # refresh docs/api.md + docs/database.md
```

## Backend conventions

- Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (sync, psycopg 3), Alembic.
- `ruff` (lint + format, line length 100) and `mypy --strict` must pass for `src` and `tests`.
- Absolute imports only (`from nimbrix_api.core...`).
- Routers: HTTP only. Services: business rules. Repositories: queries. (Phase 1+)
- Module routers declare the **full** `API_V1_PREFIX` themselves (see `api/v1/paths.py`);
  metrics label requests by the matched route template, which relies on it.
- Raise `ApiError(status, code, message)` for expected failures; never return ad-hoc error JSON.
- Every route depends on `require(permission)` (or is listed as public in `test_contracts.py`);
  create/execute mutations take `IdempotencyKey`; tenant-owned lookups use `get_owned`.
- Long-running work is a job (`modules/jobs`), never a slow request. Handlers register with `@register`.
- Agents get read-only tools only; money and policy decisions stay in deterministic code.
- Response models are explicit Pydantic classes. Never return ORM objects directly.
- Times are UTC `timestamptz`. Money is `Decimal`/`NUMERIC` with a currency code.
- Every schema change ships as an Alembic migration with a working `downgrade()`;
  `alembic check` (no model/migration drift) must pass. Constraint names come from the
  naming convention in `db/base.py`.
- Settings are `NIMBRIX_*` env vars validated in `core/config.py`. Never log setting values.

## Frontend conventions

- Next.js 16 has breaking changes versus older versions: read
  `node_modules/next/dist/docs/` before using an unfamiliar API.
- The browser calls only same-origin `/api/v1/*`; the route handler proxies to the API
  (`ADR-0006`). Never add `NEXT_PUBLIC_*` secrets or API URLs.
- Types for API payloads come from `@nimbrix/contracts`. Server state uses TanStack Query.
- Every data view has loading, empty, and error states. Accessible markup (labels,
  roles, keyboard focus) is required.
- ESLint (`--max-warnings=0`), Prettier, `tsc --noEmit`, and Vitest must pass.

## Testing expectations

- New behavior ships with tests at the lowest useful level, plus integration tests for
  anything touching SQL and E2E coverage for user-visible flows.
- Security-relevant behavior (redaction, tenant isolation, authorization denial) gets
  explicit negative tests.
- Report real command output. A skipped test is reported as skipped.

## Git

- Conventional commits (`feat:`, `fix:`, `docs:`, `chore:`, `test:`, `ci:`).
- Do not push, create remotes, rewrite history, or force-push without explicit approval.
- Commit only what you have reviewed; run `make check` first.
