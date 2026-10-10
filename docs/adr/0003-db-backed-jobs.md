# ADR-0003: Database-backed background jobs (no Celery/Redis initially)

- Status: Accepted
- Date: 2026-10-10

## Context

Imports, aggregation, forecasting, anomaly detection, and investigations must not run
inside HTTP requests. Volumes are modest (a few jobs per tenant per hour). We need
retries, idempotency, visibility (`GET /jobs/{id}`), and no duplicate work.

## Decision

Implement a **`jobs` table in PostgreSQL** and a worker process (same package as the API,
separate container) that claims work with `SELECT … FOR UPDATE SKIP LOCKED`.

Planned shape (Phase 2): `id, tenant_id, kind, payload (validated JSON), state
(queued|running|succeeded|failed|cancelled), attempts, max_attempts, run_after,
locked_by, locked_until (lease), dedupe_key (unique while active), error, timestamps`.

- Leases + heartbeats recover jobs from crashed workers.
- Exponential backoff on retryable errors; terminal failure after `max_attempts`.
- `dedupe_key` prevents duplicate scheduling of the same work (e.g., the same day's forecast).
- Job enqueue happens in the same transaction as the domain write (transactional outbox
  for free), so no job is lost or orphaned.

## Alternatives considered

- **Celery + Redis** — mature, but adds two moving parts and a second source of truth for
  job state, and the job/domain write can no longer share a transaction. Revisit only if
  measured throughput or scheduling needs exceed what the table approach handles.
- **FastAPI `BackgroundTasks`** — in-process, lost on restart, no retries. Rejected.

## Consequences

- Redis is not part of the stack (the reference diagram marks it optional).
- No top-level `workers/` package: the worker is `python -m nimbrix_api.worker` (Phase 2).
- Polling interval bounds job pickup latency (target ≤ 2 s); acceptable for batch analytics.
