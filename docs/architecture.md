# Nimbrix Architecture

> Status: **Phase 0 baseline.** This document describes the target system and marks
> clearly what exists today. Sections tagged _(planned — Phase N)_ are design, not
> implemented behavior. The source of truth for decisions is [`docs/adr/`](adr/).

## 1. Purpose and scope

Nimbrix ingests cloud cost and resource data, explains where money goes, detects
anomalies and waste, forecasts spend, produces evidence-backed recommendations,
simulates savings, and executes a very small allowlist of safe actions **only after
explicit human approval**. The entire demo runs locally with synthetic data — no cloud
account, no paid model, no credentials.

Non-goals: real-time billing (cloud billing data is inherently delayed), autonomous
cloud writes, and LLM-computed dollar figures.

## 2. System context

```mermaid
flowchart LR
  subgraph Users
    FA[FinOps analyst]
    PE[Platform engineer]
    EM[Engineering manager]
    AP[Approver]
    AU[Auditor]
  end

  Users -->|HTTPS, browser| NX[Nimbrix]

  NX -->|read-only, optional, opt-in| AWSR[AWS Cost Explorer / CUR / EC2 / CloudWatch]
  NX -.->|write, disabled by default, never in demo| AWSW[AWS write APIs]
  NX -->|optional, opt-in| LLM[LLM provider: OpenAI / Anthropic / Ollama]
  FIX[Synthetic fixtures and CSV uploads] --> NX
```

Every external dependency is optional. The default (and only Phase 0) configuration
talks to nothing outside the local Docker network.

## 3. Components

```mermaid
flowchart LR
  subgraph Browser
    UI[Next.js UI<br/>TanStack Query]
  end

  subgraph WebTier[web container]
    BFF[Route handler proxy<br/>/api/v1/*]
  end

  subgraph ApiTier[api container — modular monolith]
    API[FastAPI routers /api/v1]
    MODS[Domain modules<br/>costs · imports · resources · anomalies<br/>forecasts · recommendations · approvals<br/>remediation · audit]
    POL[Policy / approval engine]
    EXEC[Remediation executor]
    SIM[Simulation adapter]
    AGENT[LangGraph investigation agents]
  end

  subgraph WorkerTier[worker container — same codebase]
    JOBS[DB-backed job runner]
    ING[Ingestion adapters]
    ANA[Deterministic analytics]
    ML[Forecast and anomaly models]
  end

  PG[(PostgreSQL 16)]
  PROM[Prometheus scrape]

  UI -->|same origin| BFF -->|internal network| API
  API --> MODS --> PG
  API --> POL --> EXEC --> SIM
  API --> AGENT -->|read-only tenant-scoped tools| MODS
  JOBS --> ING --> PG
  JOBS --> ANA --> PG
  JOBS --> ML --> PG
  JOBS -->|claim / heartbeat| PG
  PROM -.-> API
```

| Component | Responsibility | Status |
| --- | --- | --- |
| Web (`apps/web`) | Next.js 16 UI; server-side proxy for `/api/v1/*` so the browser never needs the API URL or any secret | Phase 0: status page + proxy |
| API (`apps/api`) | FastAPI modular monolith, Pydantic v2 contracts, SQLAlchemy 2, Alembic | Phase 0: health, readiness, metrics, error envelope, request IDs, config validation |
| Worker | Separate process from the same Python package; claims DB-backed jobs | _(planned — Phase 2, see [ADR-0003](adr/0003-db-backed-jobs.md))_ |
| PostgreSQL 16 | System of record for all tenants, billing, inventory, jobs, audit | Phase 0: `tenants` table via first migration |
| Contracts (`packages/contracts`) | OpenAPI document exported from the API + generated TypeScript types | Phase 0: generated and drift-checked in CI |
| Agents | LangGraph investigation graph with read-only tools and deterministic fallback | _(planned — Phase 5)_ |
| Policy + executor | Approval state machine and allowlisted action execution (simulation first) | _(planned — Phase 6)_ |

### Request path (implemented in Phase 0)

1. Browser requests `/status` from the web origin.
2. The status component polls `GET /api/v1/health/ready` on the **same origin**.
3. The Next.js route handler validates the path (rejects `.`/`..`, blocks
   `metrics`, `docs`, `openapi.json`), forwards only allowlisted headers, and calls
   `NIMBRIX_API_INTERNAL_URL` with a 10 s timeout.
4. The API assigns or propagates `X-Request-ID`, probes PostgreSQL (`SELECT 1`) and
   compares the Alembic revision with the packaged head, then returns 200 or 503.
5. Failures at any hop surface as the standard error envelope with the request ID.

## 4. Key flows

### 4.1 Ingestion _(planned — Phase 2)_

```mermaid
sequenceDiagram
  autonumber
  actor U as Analyst
  participant W as Web
  participant A as API
  participant DB as PostgreSQL
  participant J as Worker
  participant S as Source adapter<br/>(fixtures / CSV / AWS read-only)

  U->>W: Import demo data / upload CSV
  W->>A: POST /imports/{demo|upload} + Idempotency-Key
  A->>DB: insert billing_import (state=queued, checksum) + job
  A-->>W: 202 {import_id, job_id}
  J->>DB: claim job (FOR UPDATE SKIP LOCKED)
  J->>S: fetch records (paginated, read-only)
  S-->>J: raw records (untrusted)
  J->>J: validate schema, normalize UTC + currency, dedupe by source ID/checksum
  J->>DB: upsert cost_line_items / resource_inventory with provenance
  J->>DB: import state=succeeded | partially_failed | failed (+ errors)
  W->>A: GET /imports/{id} (poll)
  A-->>W: state, counts, row-level errors
```

### 4.2 Analysis, anomaly, and investigation _(planned — Phases 3–5)_

```mermaid
sequenceDiagram
  autonumber
  participant J as Worker
  participant DB as PostgreSQL
  participant A as API
  participant G as LangGraph agent
  participant M as LLM (optional)

  J->>DB: aggregate daily cost (UTC buckets)
  J->>J: seasonal baseline + residual threshold (deterministic)
  J->>DB: anomalies (detector version, expected, observed, score, evidence refs)
  J->>DB: recommendations (evidence, assumptions, savings formula, confidence, risk)
  A->>G: POST /anomalies/{id}/investigate
  G->>A: read-only tenant-scoped tools (cost, inventory, anomaly context)
  alt LLM configured
    G->>M: redacted evidence, structured output schema
    M-->>G: hypothesis citing evidence IDs
  else no LLM key (default)
    G->>G: deterministic fallback investigator
  end
  G->>DB: investigation + agent_run (mode, evidence IDs, token/cost if any)
```

Dollar amounts, forecasts, and risk scores are always computed deterministically. The
agent may only cite and explain them.

### 4.3 Remediation with approval _(planned — Phase 6)_

```mermaid
sequenceDiagram
  autonumber
  actor R as Requester
  actor P as Approver
  participant A as API
  participant POL as Policy engine
  participant DB as PostgreSQL
  participant X as Executor
  participant SIM as Simulation adapter

  R->>A: POST /approvals {action: simulate_stop_nonprod_instance, payload}
  A->>POL: validate action is allowlisted, tenant owns resource, payload schema
  POL-->>A: impact preview + rollback plan
  A->>DB: approval_request (payload hash, expiry, status=pending) + audit_event
  P->>A: POST /approvals/{id}/approve
  A->>POL: approver ≠ requester (if configured), not expired, hash unchanged
  A->>DB: status=approved + audit_event
  R->>A: POST /remediations + Idempotency-Key
  A->>X: execute (dry-run default)
  X->>DB: revalidate resource state immediately before execution
  X->>SIM: simulate stop (no cloud call)
  SIM-->>X: simulated result + before/after cost estimate
  X->>DB: remediation_execution (mode=simulation, result, verification) + audit_event
```

## 5. Trust boundaries

```mermaid
flowchart TB
  subgraph TB1[Untrusted: internet / browser]
    B[Browser]
  end
  subgraph TB2[Trusted: web server]
    P[BFF proxy]
  end
  subgraph TB3[Trusted: internal network]
    API[API]
    WK[Worker]
    DB[(PostgreSQL)]
  end
  subgraph TB4[Untrusted data sources]
    CSV[Uploaded CSV]
    AWS[AWS billing + tags + resource names]
    LLMO[LLM output]
  end

  B -- "TB-1: user input, session" --> P
  P -- "TB-2: allowlisted paths and headers" --> API
  API --> DB
  WK --> DB
  CSV -- "TB-3: schema validation, size limits" --> WK
  AWS -- "TB-4: read-only role, validated, tags treated as data" --> WK
  LLMO -- "TB-5: structured output validation, never authoritative" --> API
```

| Boundary | Threats | Controls (✅ implemented · 🔜 planned) |
| --- | --- | --- |
| TB-1 Browser → web | XSS, CSRF, session theft | ✅ No secrets or API URL in the bundle; ✅ same-origin proxy · 🔜 secure cookies + CSRF (Phase 1/8), CSP (Phase 8) |
| TB-2 Web → API | Path traversal, header smuggling, internal endpoint exposure | ✅ Segment validation, header allowlists both ways, `metrics`/`docs`/`openapi.json` blocked at the proxy, 10 s timeout |
| API → DB | SQL injection, cross-tenant reads | ✅ SQLAlchemy parameterized queries, statement timeout · 🔜 tenant-scoped repositories enforced server-side (Phase 1) |
| TB-3 CSV | Malicious/oversized files, formula injection | 🔜 Size limits, strict schema, no formula evaluation (Phase 2/8) |
| TB-4 AWS | Over-privileged credentials, tag-borne prompt injection | 🔜 Explicit read-only role, no stored raw keys, tags treated as data (Phase 2) |
| TB-5 LLM | Prompt injection, hallucinated resources or dollars | 🔜 Structured outputs citing evidence IDs, deterministic numbers, no write tools (Phase 5) |
| Logs/telemetry | Secret or billing data leakage | ✅ JSON logs with URL-credential and key=value redaction; ✅ config errors never echo values; ✅ readiness never returns raw DB errors |

## 6. Data freshness (batch, not real time)

Nimbrix is a **batch analytics** system. It never presents data as real-time.

| Source | Typical freshness | How Nimbrix treats it |
| --- | --- | --- |
| Synthetic fixtures | Instant, deterministic | Labeled `demo` everywhere |
| CSV upload | As of the file's contents | Stored with import timestamp + checksum |
| AWS Cost Explorer | Updated several times per day; recent days are estimates and can change | Re-ingest a trailing window; show "as of" timestamps _(Phase 2)_ |
| AWS CUR (S3/Athena) | Delivered up to several times daily; finalized after month close | Treat current month as provisional _(Phase 2)_ |
| CloudWatch metrics | Minutes; aggregated over a utilization window | Store window + statistic used as evidence _(Phase 2)_ |

Late-arriving records are handled by idempotent upserts keyed on source identifiers, so
re-imports correct prior values instead of duplicating them.

## 7. Simulated versus live behavior

| Capability | Simulated (default, only mode in demo) | Live (opt-in, later phases) |
| --- | --- | --- |
| Billing data | Synthetic fixtures, CSV | AWS Cost Explorer / CUR via read-only role |
| Investigations | Deterministic fallback investigator | Configured LLM provider, still read-only |
| Remediation | `simulate_stop_nonprod_instance` via simulation adapter | **Not implemented.** Requires a separate feature flag, narrow IAM role, resource allowlist, and approval ([ADR-0005](adr/0005-safety-policy.md)) |

Phase 0 enforcement: `NIMBRIX_LIVE_ACTIONS_ENABLED=true` is **rejected at startup**,
the readiness payload reports `remediation_mode: "simulation_only"`, and the UI header
shows "Simulation mode · no cloud connections" on every page.

## 8. Deployment topology (Phase 0)

```mermaid
flowchart LR
  subgraph Host[Developer machine, 127.0.0.1 only]
    W[web :3200] --> A[api :8200]
    A --> D[(postgres :5440)]
    M[migrate one-shot] --> D
  end
```

`docker compose up --build --wait` starts PostgreSQL → runs `alembic upgrade head` in a
one-shot `migrate` container → starts the API (waits for successful migration) → starts
the web app (waits for a healthy API). Containers run as a non-root user with health
checks. Production deployment on AWS (ECS/Fargate, RDS, Secrets Manager) is
_(planned — Phase 9)_ and will never be applied without explicit approval.

## 9. Repository layout

```text
apps/api/                 FastAPI app, Alembic migrations (packaged), tests/{unit,integration}
apps/web/                 Next.js app, Vitest component/unit tests
packages/contracts/       openapi.json + generated TypeScript types (drift-checked in CI)
infrastructure/docker/    api.Dockerfile, web.Dockerfile
tests/e2e/                Playwright tests against the running stack
docs/                     architecture, ADRs, API, security, progress
```

Deviations from the proposed layout, with reasons:

- **`workers/`** is not a separate top-level package: the worker is a second entrypoint
  of the API package so it shares domain models, repositories, and tenant guards
  ([ADR-0003](adr/0003-db-backed-jobs.md)). It arrives in Phase 2.
- **API integration tests** live in `apps/api/tests/integration` (not root
  `tests/integration`) so they run inside the API's locked Python environment.
  Cross-service E2E tests live in root `tests/e2e`. `tests/performance` is added in Phase 8.
- **`infrastructure/terraform/`** is added in Phase 9.

## 10. Engineering conventions

See [AGENTS.md](../AGENTS.md) for the full list. Highlights: UTC everywhere; `Decimal`
for money with an explicit ISO currency code; UUID primary keys; explicit constraint
naming; every mutation endpoint takes an `Idempotency-Key`; stable error envelope;
module routers declare the full `/api/v1` prefix (metrics label by route template).
