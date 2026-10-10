# Nimbrix Progress Tracker

| Phase | Name | Status | Date |
| --- | --- | --- | --- |
| 0 | Discovery, design, and engineering foundation | ✅ Complete | 2026-10-10 |
| 1 | Data model, API foundation, authentication, tenancy | ⏸ Awaiting approval | — |
| 2 | Synthetic data and AWS cost/inventory ingestion | Not started | — |
| 3 | Cost analytics, budgets, optimization rules | Not started | — |
| 4 | ML forecasting and cost anomaly detection | Not started | — |
| 5 | LangGraph AI investigation agents | Not started | — |
| 6 | Approvals, policy engine, safe remediation | Not started | — |
| 7 | Professional full-stack dashboard | Not started | — |
| 8 | Security, observability, reliability, testing | Not started | — |
| 9 | Deployment, benchmarks, documentation, release | Not started | — |

---

## Phase 0 — Discovery, design, and engineering foundation

**Status: complete** (all "done when" criteria verified with the commands below).

### Discovery

- Started from an empty directory (`~/Nimbrix` did not exist); no user files to preserve.
- Toolchain verified: Docker 29.8.2 (Compose v2), Node 25.6.1 / npm, uv 0.10.4 with
  CPython 3.12.12 (system Python is 3.9 and is not used), gh CLI. `terraform`,
  `actionlint`, and `shellcheck` are not installed locally (actionlint was run via its
  official Docker image, which bundles shellcheck).
- Ports 3000 and 8000 are already used by another local project, so Nimbrix defaults to
  web **3200**, API **8200**, PostgreSQL **5440** (all configurable, all `127.0.0.1`).
- Spec contradictions resolved: none blocking. Clarifications recorded as ADRs:
  no Redis/Celery initially (ADR-0003), worker lives inside the API package rather than a
  top-level `workers/` package (ADR-0003), browser→API through a same-origin proxy (ADR-0006).

### Implemented

| Requirement | Result |
| --- | --- |
| Architecture document | `docs/architecture.md`: context, component, ingestion / analysis / remediation sequence diagrams, trust boundaries, data freshness, simulated vs live |
| ADRs | 0001 modular monolith · 0002 PostgreSQL · 0003 DB-backed jobs · 0004 model provider · 0005 safety policy · 0006 same-origin proxy |
| FastAPI backend | App factory, typed settings with fail-fast validation, JSON logging with redaction, request-ID middleware, error envelope, Prometheus metrics |
| Health endpoints | `GET /api/v1/health/live`, `GET /api/v1/health/ready` (DB `SELECT 1` + Alembic revision == head), `GET /api/v1/metrics` |
| Database | SQLAlchemy 2 + psycopg 3; naming convention; first migration `0001_create_tenants` (UUID PK, unique slug, slug format check, UTC timestamps) |
| Frontend | Next.js 16 + Tailwind 4 + TanStack Query; `/status` page with loading / not-ready / unreachable states and retry; `/` redirects to `/status`; same-origin `/api/v1/*` proxy; `/healthz` |
| Contracts | `packages/contracts`: OpenAPI exported from code + generated TS types consumed by the web app; CI fails on drift |
| Docker | Multi-stage, non-root images for API and web; Compose with postgres → one-shot migrate → api → web dependency chain and health checks |
| Tooling | Ruff, mypy `--strict`, pytest, ESLint 10, Prettier, `tsc`, Vitest, Playwright; `Makefile`; `.editorconfig`; `.gitignore`; `.dockerignore`; `.env.example` |
| CI | `.github/workflows/ci.yml`: API (lint, format, mypy, pytest with PG 16 service, migration up/check/down), web (lint, prettier, tsc, vitest, build), contracts drift, Docker Compose stack + Playwright |
| Agent docs | `AGENTS.md` is the single engineering guide. The tool-specific instruction file requested by the spec exists locally but is intentionally untracked, per the owner's repository preferences |

### Verification (real results, 2026-10-10, macOS arm64)

| Command | Result |
| --- | --- |
| `docker compose down -v` then `make up` (clean images and volume) | All services healthy in ~28 s; `migrate` exited 0 after `Running upgrade -> 0001_create_tenants` |
| `curl 127.0.0.1:8200/api/v1/health/ready` | `200`, `status: ready`, migrations `0001_create_tenants / 0001_create_tenants` |
| `curl -I 127.0.0.1:3200/` | `307` → `/status` |
| `make lint` | Ruff: all checks passed; ruff format: clean; ESLint: 0 problems; Prettier: clean |
| `make typecheck` | mypy strict: no issues in 33 source files; `tsc` (web, contracts, e2e): clean |
| `make test-api` | **39 passed** |
| `make test-integration` (real PostgreSQL 16.10) | **3 passed** — upgrade from empty DB, readiness 503→200 across upgrade, re-upgrade no-op, downgrade to base, constraint enforcement, zero model/migration drift |
| `make test-web` | **25 passed** (3 files: proxy, readiness client, status component) |
| `make build` | Next.js production build succeeded |
| `make e2e` (Playwright, Chromium, against the Compose stack) | **4 passed** |
| `alembic check` | No new upgrade operations detected |
| `actionlint` 1.7.12 (Docker) | Exit 0, no findings |
| `docker compose config --quiet` | Valid |
| Manual: stop `api` container | Web proxy returns `502 upstream_unavailable` envelope with the caller's request ID |
| Manual: stop `postgres` container | API readiness `503`, `database unreachable`, no driver details leaked |

The GitHub Actions workflow has been validated statically (actionlint) and every step
was run locally, but it has **not executed on GitHub** because nothing has been pushed.

### Dependency license review

- **Python runtime:** MIT / BSD / Apache-2.0 / PSF, plus **psycopg 3 (LGPL-3.0)** used
  unmodified as an installed library (compatible with any license for this project).
  Dev-only: `pathspec` (MPL-2.0, via mypy).
- **Node:** 416 MIT, 34 Apache-2.0, 19 ISC, BSD, CC0, BlueOak, 0BSD; MPL-2.0 packages
  (build tooling, e.g. Lightning CSS); `@img/sharp-libvips` (LGPL-3.0-or-later, optional
  native image library pulled in by Next.js, unmodified); `caniuse-lite` data (CC-BY-4.0).
- **Datasets:** none used. Phase 2 fixtures will be generated synthetically in-repo.
- No dependency imposes obligations that conflict with how they are used here.

### Known limitations

- No authentication, users, RBAC, or tenant scoping yet (Phase 1). Only health/metrics endpoints exist.
- `/api/v1/metrics` and `/api/v1/docs` are unauthenticated on the API port (bound to
  `127.0.0.1`; blocked at the web proxy). Production exposure policy is Phase 8/9.
- `npm audit`: 5 high-severity advisories in the **dev-only** ESLint chain
  (`braces` ← `micromatch` ← `fast-glob` ← `@next/eslint-plugin-next`); no non-breaking fix
  available; not present in the runtime image.
- CI has not run on GitHub yet (no remote; publishing requires approval).
- No worker process yet (Phase 2); no OpenTelemetry tracing yet (Phase 8).
- No project license chosen yet (owner decision).

### Next-phase dependencies (Phase 1)

- Owner approval to start Phase 1.
- Decisions with defaults proposed for Phase 1 (will proceed with these unless told otherwise):
  demo auth via signed, HTTP-only session cookie issued by the API and forwarded by the
  proxy; seeded `viewer`, `analyst`, `approver`, `admin` users in one demo tenant plus a
  second tenant for leakage tests; OIDC documented as the production path.
- Builds on: `tenants` table, error envelope (`ApiError`), request IDs, the proxy header
  allowlist (will add the session cookie), and the migration test harness.
