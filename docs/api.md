# Nimbrix API Reference

Base path: `/api/v1`. The machine-readable contract is
[`packages/contracts/openapi.json`](../packages/contracts/openapi.json), exported from the
code (`make contracts`) and drift-checked in CI. Interactive docs are served by the API
container at `http://127.0.0.1:8200/api/v1/docs` (not exposed through the web origin).

## Conventions (all endpoints)

| Concern | Rule |
| --- | --- |
| Correlation | Every response carries `X-Request-ID`. A caller-supplied value is reused only if it matches `^[A-Za-z0-9._-]{8,64}$`; otherwise a new one is generated. |
| Errors | Non-2xx responses (except readiness 503) use the envelope below. Validation errors list offending fields but **never echo submitted values**. Unexpected errors return a generic 500. |
| Time | ISO 8601 with offset; all server timestamps are UTC. |
| Money | Decimal strings with an explicit currency code _(from Phase 3)_. |
| Mutations | Require `Idempotency-Key` _(from Phase 1)_. |
| Pagination | Cursor-based `limit`/`cursor` _(from Phase 1)_. |

```json
{
  "error": {
    "code": "not_found",
    "message": "Not Found",
    "request_id": "6f1c0e9b6d0c4a0f9f6b1f2f3c4d5e6f",
    "details": [{ "field": "path.item_id", "message": "Input should be a valid integer" }]
  }
}
```

## Implemented endpoints (Phase 0)

### `GET /api/v1/health/live`

Process liveness; never touches the database.

```json
{ "status": "ok", "service": "nimbrix-api", "version": "0.1.0" }
```

### `GET /api/v1/health/ready`

`200` when PostgreSQL answers `SELECT 1` **and** the database's Alembic revision equals
the packaged migration head; otherwise `503` with the same body shape. Failure details
are summarized ("database unreachable"); raw driver errors are logged server-side (redacted),
never returned.

```json
{
  "status": "ready",
  "service": "nimbrix-api",
  "version": "0.1.0",
  "environment": "local",
  "remediation_mode": "simulation_only",
  "checks": {
    "database": { "status": "ok", "latency_ms": 2.04, "detail": null },
    "migrations": {
      "status": "ok",
      "current_revision": "0001_create_tenants",
      "expected_revision": "0001_create_tenants"
    }
  }
}
```

### `GET /api/v1/metrics`

Prometheus text format. Counters `nimbrix_http_requests_total{method,route,status}` and
histogram `nimbrix_http_request_duration_seconds{method,route}`. `route` is the route
**template** (e.g. `/api/v1/resources/{id}`) or `__unmatched__`, which bounds label
cardinality. Blocked at the web proxy; scrape it on the internal network.

## Planned surface (not implemented yet)

| Area | Endpoints | Phase |
| --- | --- | --- |
| Identity | `GET /me`, `GET /tenants/current` | 1 |
| Connections | `GET /connections`, `POST /connections/aws/validate` | 1–2 |
| Imports & jobs | `POST /imports/demo`, `POST /imports/upload`, `GET /imports/{id}`, `GET /jobs/{id}` | 2 |
| Resources | `GET /resources`, `GET /resources/{id}` | 2 |
| Costs | `GET /costs/summary`, `GET /costs/timeseries`, `GET /costs/breakdown` | 3 |
| Recommendations | `GET /recommendations`, `GET /recommendations/{id}`, `POST /recommendations/{id}/simulate` | 3 |
| Forecasts | `GET /forecasts`, `POST /forecasts/run` | 4 |
| Anomalies | `GET /anomalies`, `GET /anomalies/{id}`, `POST /anomalies/{id}/investigate` | 4–5 |
| Approvals | `POST /approvals`, `GET /approvals`, `POST /approvals/{id}/approve`, `POST /approvals/{id}/reject` | 6 |
| Remediation | `POST /remediations`, `GET /remediations/{id}` | 6 |
| Audit | `GET /audit-events` | 1 (writes), 6 (UI) |
