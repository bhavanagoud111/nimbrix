# Nimbrix Progress Tracker

| Phase | Name | Status | Date |
| --- | --- | --- | --- |
| 0 | Discovery, design, and engineering foundation | ✅ Complete | 2026-10-10 |
| 1 | Data model, API foundation, authentication, tenancy | ✅ Complete | 2026-10-10 |
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

### Next-phase dependencies (Phase 1) — resolved

- Owner approval to start Phase 1.
- Decisions with defaults proposed for Phase 1 (will proceed with these unless told otherwise):
  demo auth via signed, HTTP-only session cookie issued by the API and forwarded by the
  proxy; seeded `viewer`, `analyst`, `approver`, `admin` users in one demo tenant plus a
  second tenant for leakage tests; OIDC documented as the production path.
- Builds on: `tenants` table, error envelope (`ApiError`), request IDs, the proxy header
  allowlist (will add the session cookie), and the migration test harness.

---

## Phase 1 — Data model, API foundation, authentication, tenancy

**Status: complete.** Approved by the owner ("implement all the phases").

### Implemented

- Migration `0002_core_domain`: users, roles, memberships, sessions, cloud_connections,
  billing_imports, cost_line_items, resource_inventory, budgets, budget_alerts, anomalies,
  forecasts, recommendations, investigations, agent_runs, approval_requests,
  remediation_executions, audit_events, idempotency_keys. UUID PKs, explicit `tenant_id`
  on every tenant-owned table, unique constraints, composite indexes starting with
  `tenant_id`, CHECK constraints for every status vocabulary, `NUMERIC(18,6)` money with
  an ISO currency CHECK. A trigger makes `audit_events` append-only (UPDATE/DELETE raise).
- Demo auth (`/auth/login`, `/auth/logout`), `/me`, `/tenants/current`; scrypt password
  hashes; hashed, expiring, revocable sessions; CSRF token bound to each session.
- RBAC with five roles; `require(permission)` on every endpoint; denials audited.
- Tenant scoping from the session only; cross-tenant ID access returns 404 and is audited.
- Connections: `GET/POST /connections`, `GET /connections/{id}` — metadata only, raw
  key material rejected, role ARN must match account.
- `GET /audit-events` with keyset pagination; audit writer with payload redaction.
- Idempotency-Key enforcement and stored-response replay for mutations.
- `nimbrix-seed`: roles, tenants `acme-demo` and `globex-demo`, users
  `{viewer,analyst,approver,auditor,admin}@acme.demo` and `admin@globex.demo`.
- Web proxy forwards only `nimbrix_*` cookies and `X-CSRF-Token` ([ADR-0007](adr/0007-authentication-and-tenancy.md)).

### Verification

| Command | Result |
| --- | --- |
| `uv run pytest` (unit + integration, PostgreSQL 16) | **64 passed** |
| `uv run mypy src tests` / `ruff check` / `ruff format --check` | clean |
| `alembic upgrade head` → `downgrade 0001` → `upgrade head` → `alembic check` | OK, no drift |
| `npm test` (web) | **27 passed** |

Key security tests: fail-closed unauthenticated access, revoked/expired sessions,
CSRF missing/forged, RBAC matrix denials with audit, idempotency reuse (409), credential
rejection on connections, cross-tenant 404 + audit, client tenant header ignored,
append-only audit enforced by the database.

### Known limitations

- OIDC is designed (ADR-0007) but not implemented; demo auth is blocked in production by default.
- No user/role management API; users come from the seed.

---

## Phase 2 — Synthetic data and AWS cost/inventory ingestion

**Status: complete.**

### Implemented

- Migration `0003_jobs`: `jobs` (partial unique index for active dedupe keys) and `import_files`.
- DB-backed job queue + `nimbrix-worker` (SKIP LOCKED claims, leases, heartbeats, retry with
  backoff, crash recovery, Prometheus metrics on :9101). Compose runs it as the `worker` service.
- Reproducible synthetic generator (120 days, 3 accounts, 4 regions, 10 services, labeled spike,
  idle instance, and more; see [ingestion.md](ingestion.md)).
- Adapters: synthetic, CSV (row-level validation), AWS Cost Explorer, CUR via Athena, EC2 +
  CloudWatch inventory, STS assume-role with external ID; `validate` and `sync` endpoints.
- Endpoints: `POST /imports/demo`, `POST /imports/upload`, `GET /imports`, `GET /imports/{id}`,
  `GET /jobs/{id}`, `GET /resources`, `GET /resources/{id}`, `POST /connections/aws/validate`,
  `POST /connections/{id}/sync`.
- Idempotent upserts with inserted/updated/unchanged/rejected accounting; checksum duplicates.
- Opt-in seeding: `make demo` (or `NIMBRIX_DEMO_SEED=true`) seeds users and queues demo imports.
- Fixtures in `data/fixtures/`: valid, malformed, late-arriving, empty, mixed-currency, non-UTF-8.

### Verification

| Command | Result |
| --- | --- |
| `uv run pytest` (unit + integration on PostgreSQL 16) | **100 passed** |
| `ruff`, `ruff format --check`, `mypy --strict src tests` | clean |
| `NIMBRIX_DEMO_SEED=true docker compose up --build --wait` | migrate + seed OK; worker imported both demo tenants (`job_succeeded` ×2) |
| Login + `GET /imports` through the web proxy (curl) | `demo succeeded 7760 rows, 68 resources, 2026-06-12 → 2026-10-09` |

AWS adapters are tested only against botocore Stubber responses. **No real AWS account was
used**; live behavior against AWS is unverified.

### Known limitations

- CUR/Athena adapter has no API/UI entry point yet (needs Athena settings per connection).
- CloudWatch memory metrics are not collected. Prices come from a reference list-price table.

---

## Phase 3 — Cost analytics, budgets, and optimization rules

**Status: complete.** Details: [analytics.md](analytics.md).

### Implemented

- `GET /costs/summary|timeseries|breakdown|unit-costs|dimensions` with account/service/region/
  environment/team/tag/date/currency filters; UTC, month-boundary, and partial-day rules;
  allocated vs unallocated; MTD vs previous comparable period; top cost drivers.
- Budgets: `GET/POST /budgets`, `GET /budgets/alerts`, `POST /budgets/alerts/{id}/acknowledge`;
  actual + projected utilization (forecast when available, else run rate); deduplicated alerts.
- Five transparent rules (`rules-v1`) with eligibility checks, evidence, assumptions, savings
  formula, owner, confidence, risk, and explicit `insufficient_evidence` handling.
- `POST /recommendations/{id}/simulate` (estimated delta, range, excluded costs; labeled
  simulation / not realized), `POST /recommendations/{id}/dismiss`, `POST /analysis/run`.
- `analyze_tenant` job runs automatically after every successful import.

### Verification

| Command | Result |
| --- | --- |
| `uv run pytest` (unit + integration on PostgreSQL 16) | **123 passed** |
| `ruff`, `mypy --strict src tests` | clean |

Precision tests assert exact decimal sums (0.1 + 0.2 = 0.3 per day), currency separation,
zero-data responses, gap filling, month buckets, top-N grouping, unit costs, and exact savings
(e.g. idle `m5.2xlarge` = 0.384 × 730 = 280.32).

### Known limitations

- Savings use reference list prices (no RI/SP/EDP discounts).
- Business-metric unit economics are not computed (no metric source).

---

## Phase 4 — ML forecasting and cost anomaly detection

**Status: complete.** Details and measured results: [ml.md](ml.md).

### Implemented

- Versioned feature preparation (`daily-cost-v1`): missing-day handling, training-only
  winsorization, leakage-safe splits.
- Seasonal-naive baseline + damped Holt-Winters; rolling-origin backtests (MAE, WAPE,
  cross-conformal interval coverage); selection only if the alternative beats the baseline.
- 7/30-day forecasts with intervals, model/feature versions, and training window; honest
  `insufficient_data` and limited-history labels.
- `seasonal-residual-v1` detector with low/medium/high sensitivity, window dedupe, evidence
  (daily values, contributors), lifecycle with audited transitions.
- Offline labeled evaluation harness (`python -m nimbrix_api.modules.ml.evaluation`).
- Endpoints: `GET /forecasts`, `GET /forecasts/{id}`, `POST /forecasts/run`, `GET /anomalies`,
  `GET /anomalies/{id}`, `POST /anomalies/{id}/status`. Budget projections use forecasts.
- Analysis pipeline (anomalies → forecasts → recommendations → budget alerts) and a daily
  scheduler in the worker that never queues duplicate work.

### Verification

| Command | Result |
| --- | --- |
| `uv run pytest` (unit + integration on PostgreSQL 16) | **140 passed** |
| `ruff`, `mypy --strict src tests` | clean |
| `python -m nimbrix_api.modules.ml.evaluation` | medium sensitivity: precision 0.987, recall 0.980; Holt-Winters 30-day WAPE 3.50% vs baseline 4.81% |

### Known limitations

- Metrics come from synthetic series; no real customer data was available.
- Only spend increases are detected; drops are not flagged.

---

## Phase 5 — LangGraph AI investigation agents

**Status: complete** (live LLM providers unverified — no credentials were used). Details: [investigation-agents.md](investigation-agents.md).

### Implemented

- Typed LangGraph state machine: triage → collect_evidence → forecast_context → hypothesize
  (bounded retry) → synthesize_recommendations → policy_review → report, with five roles.
- Read-only, tenant-scoped evidence tools in `READ ONLY` transactions; no write or cloud tools.
- Structured Pydantic outputs with evidence-ID citations, confidence labels, and
  `insufficient_evidence`; grounding validation against invented IDs, accounts, and amounts.
- Provider adapters: Anthropic (official SDK, structured outputs), OpenAI and Ollama (HTTP),
  deterministic fallback (default). Mode shown on every investigation.
- Budgets (steps, time, LLM calls), cancellation, token/cost tracking, trace IDs, redaction.
- Policy engine core (`modules/remediation/registry.py`) used read-only by the agents.
- Endpoints: `POST /anomalies/{id}/investigate`, `GET /investigations`, `GET /investigations/{id}`,
  `GET /investigations/{id}/report.md`, `POST /investigations/{id}/cancel`.

### Verification

| Command | Result |
| --- | --- |
| `uv run pytest` | **170 passed** |
| `ruff`, `mypy --strict src tests` | clean |

The demo spike yields a completed deterministic report: "8 new c5.4xlarge resources (team
search, environment dev, account 333333333333) … add 130.56/day", high confidence, all
citations resolvable. Tests cover malformed output, unavailable provider, prompt-injected
output, step budget, cancellation, cross-tenant tool calls, and read-only enforcement.

---

## Phase 6 — Approvals, policy engine, and safe remediation

**Status: complete.** Details: [remediation.md](remediation.md).

### Implemented

- Action registry with exactly one action, `simulate_stop_nonprod_instance`; strict payload schema.
- Policy engine (10 checks) evaluated at request, approval, and pre-execution; impact preview
  and rollback plan for every request.
- Approval state machine with expiry, rejection, separation of duties, payload-hash binding,
  tenant ownership, replay prevention; recommendation status follows the approval.
- Simulation adapter (migration `0004_simulation_state`), job-based execution with
  revalidation, verification, retries, idempotent crash recovery.
- Endpoints: `GET /actions`, `POST /approvals/preview`, `POST /approvals`, `GET /approvals`,
  `GET /approvals/{id}`, `POST /approvals/{id}/approve`, `POST /approvals/{id}/reject`,
  `POST /remediations`, `GET /remediations`, `GET /remediations/{id}`.

### Verification

| Command | Result |
| --- | --- |
| `uv run pytest` | **187 passed** |
| `alembic upgrade head` + `alembic check` | no drift |

Tests: full flow, six policy denials, unauthorized approval, self-approval, tampered hash,
tampered stored payload, expiry, duplicate execution, stale inventory, state change before
approval, missing rollback plan, rejection, worker crash/retry.

### Known limitations

- No real AWS write adapter exists (by design); the documented preconditions apply first.

---

## Phase 7 — Professional full-stack dashboard

**Status: complete.**

### Implemented

- Dark-first, responsive design system (cards, stats, badges, buttons, tables, form fields,
  loading/error/empty states, skip link, keyboard-focus styles, mobile menu).
- Typed API client generated from OpenAPI (`openapi-fetch` + `@nimbrix/contracts`), CSRF and
  Idempotency-Key middleware, 401 → login redirect, TanStack Query, polling for jobs,
  investigations, and executions.
- Pages: Login, Overview, Cost Explorer (URL-synced filters, drill-down, unit costs), Budgets
  (create, alerts, acknowledge), Anomalies (list, detail with observed-vs-expected chart,
  contributors, lifecycle), Forecasting (scope/horizon, interval chart, backtest table, run),
  Recommendations (list, detail with eligibility, evidence, simulation, request/dismiss),
  Investigation Workspace (graph progress, hypotheses with evidence anchors, proposed actions,
  rejected model output, Markdown export, cancel), Approval Center (payload + hash, policy
  checks, impact, rollback, approve/reject/execute, outcome, audit trail), Resource Inventory
  (filters, detail, policy check, request), Audit Timeline (filter, pagination),
  Integrations & Settings (demo import, CSV upload, imports with row errors, AWS connections,
  action registry, access), public System status.
- Demo/live/simulation/model provenance labels on every relevant view; savings always labeled
  as estimates.
- Next.js `proxy.ts` optimistic login redirect with a safe `next` parameter.

### Verification

| Command | Result |
| --- | --- |
| `npm test` (Vitest) | **39 passed** |
| `npm run lint`, `tsc --noEmit`, `npm run build` | clean; 22 routes built |
| `npx playwright test` against `make demo` stack | **12 passed** (re-run: 12 passed) |

E2E covers the complete demo path (spike → anomaly → investigation → recommendation →
simulation → approval → simulated execution → audit), policy-denied production stop,
self-approval denial, viewer read-only behavior, backend failure with retry, login redirect,
and unauthenticated API access.

---

## Phase 8 — Security, observability, reliability, and testing

**Status: complete.** Details: [threat-model.md](threat-model.md), [operations.md](operations.md), [security.md](security.md).

### Implemented

- OpenTelemetry tracing (opt-in OTLP/HTTP): HTTP routes, SQL (no parameters), one span per job and
  per agent node; `trace_id`/`span_id` in JSON logs.
- Prometheus metrics: HTTP, jobs, import latency and rows, agent model calls and tokens,
  investigations, remediation outcomes, approval decisions, policy denials, auth outcomes, rate
  limiting, and DB-backed gauges (recommendations by status, jobs by state).
- `observability` compose profile: Prometheus + Grafana with a provisioned 10-panel dashboard.
- Security headers (API and web CSP), request body limit (declared and streamed), in-process
  rate limits (login and general), trusted `X-Forwarded-For` handling.
- Retention job (idempotency records, sessions, raw uploads) and documented deletion, backup,
  restore, DR limits, and least-privilege IAM.
- Threat model covering prompt injection, tenant leakage, malicious CSV, IAM misuse, approval
  bypass, double execution, stale cloud state, session attacks, telemetry leakage.
- Contract tests (committed OpenAPI == code, error envelope on every operation, auth on every
  non-public route, Idempotency-Key on mutations), load-test tool for cost queries.
- CI: gitleaks (full history), pip-audit, `npm audit --omit=dev`, dependency review on PRs,
  observability profile smoke check.

### Verification

| Command | Result |
| --- | --- |
| `uv run pytest` (unit + integration) | **201 passed** |
| `npm test` / lint / tsc / build | 39 passed / clean |
| `make e2e` (with CSP and rate limits active) | **12 passed** |
| `docker compose --profile observability up` | Prometheus targets `nimbrix-api` and `nimbrix-worker` **up**; Grafana dashboard provisioned (10 panels) |
| gitleaks (git history) | no leaks (2 fake test keys allowlisted explicitly) |
| pip-audit (runtime) / `npm audit --omit=dev` | no known vulnerabilities / 0 vulnerabilities |
| actionlint | clean |
| Load smoke test (200 req × 5 endpoints, concurrency 10, API direct) | all 200; p95 53–103 ms (full benchmark in Phase 9) |

### Known limitations

- Rate limits are per process; tenant isolation is not backed by PostgreSQL RLS; web CSP allows inline scripts.
- Tracing was verified with an in-memory exporter; no external collector was run.

---

## Phase 9 — Deployment, benchmarks, documentation, and release preparation

**Status: complete, except publication** (creating/pushing a GitHub repository needs the owner's approval).

### Implemented

- `make demo` / `NIMBRIX_DEMO_SEED=true docker compose up --build` starts the full demo end to end;
  seeding happens only with the opt-in flag. `scripts/wait-for-demo.sh` waits for analyzed data.
- AWS deployment: Terraform for ECS Fargate (web, api, worker, migrate), RDS PostgreSQL 16 with an
  RDS-managed secret, Secrets Manager, ALB (TLS 1.3, HTTP→HTTPS), Service Connect, least-privilege
  roles and security groups; [deployment.md](deployment.md). `terraform fmt` + `validate` pass and
  run in CI. **No plan or apply was run.** API supports composing the DB URL from injected parts.
- Documentation: architecture refreshed to implemented state, generated API reference (55
  endpoints) and database schema, demo script, benchmarks, deployment, plus the earlier
  ingestion/analytics/ML/agents/remediation/security/threat-model/operations docs.
- Benchmarks with hardware, data sizes, and methodology ([benchmarks.md](benchmarks.md)).
- README with screenshots, implemented features, quickstart, architecture, demo, tests, measured
  performance, limitations, roadmap, and license status.

### Verification

See the final verification table at the end of this file (clean checkout run).

## Final verification (clean checkout, 2026-10-10)

Fresh `git clone` of commit `3dc812b` into an empty directory, `cp .env.example .env`, alternate
ports, isolated compose project:

| Step | Result |
| --- | --- |
| `NIMBRIX_DEMO_SEED=true docker compose up -d --build --wait` | postgres, api, worker, web healthy; migrate exited 0 |
| `scripts/wait-for-demo.sh` | demo data imported and analyzed |
| `uv sync --frozen`, `ruff check`, `ruff format --check`, `mypy --strict src tests` | clean |
| `uv run pytest` (unit + integration on the clean stack's PostgreSQL) | **202 passed** |
| `npm ci`, `npm run lint`, `npm run typecheck`, `npm test`, `npm run build` | clean; **39 passed**; build succeeded |
| `npx playwright test` | **12 passed** |

The first clean-checkout attempt failed: `apps/web/public/` was empty and therefore not in git, so
the web image could not build. Fixed in `3dc812b` (tracked `robots.txt`); the run above is after
the fix. GitHub Actions has not run yet because nothing has been pushed.

## Overall known limitations

- Demo authentication only (OIDC designed, not implemented); production startup refuses demo auth unless explicitly acknowledged.
- No live cloud remediation (by design); AWS read adapters verified only against stubs; CUR has no API entry point.
- LLM providers verified only against mocked responses.
- Reference list prices; CPU-only utilization; ML metrics measured on synthetic data.
- Application-level tenant isolation (no RLS); per-process rate limits; web CSP allows inline scripts.
- Terraform validated but never planned or applied.
- No license chosen; repository not published.
