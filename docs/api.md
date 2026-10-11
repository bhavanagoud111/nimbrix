# Nimbrix API Reference

Base path: `/api/v1`. Machine-readable contract: [`packages/contracts/openapi.json`](../packages/contracts/openapi.json)
(exported from code by `make contracts`, drift-checked in CI and by `test_committed_contract_matches_code`).
Interactive docs: `http://127.0.0.1:8200/api/v1/docs` on the API container (not exposed through the web origin).

## Conventions

| Concern | Rule |
| --- | --- |
| Authentication | Session cookie `nimbrix_session` (browser) or `Authorization: Bearer <session token>`. Every route except health, metrics, and login requires it (contract-tested). |
| CSRF | Cookie-authenticated mutations must send `X-CSRF-Token` (value from login / `nimbrix_csrf` cookie). |
| Authorization | Role permissions (`viewer`, `analyst`, `approver`, `auditor`, `admin`); denials return 403 and are audited. |
| Tenancy | Fixed by the session; other tenants' IDs return 404. Client-supplied tenant IDs are ignored. |
| Idempotency | Create/execute mutations require `Idempotency-Key` (8–128 printable ASCII). Same key + same body replays the stored response with `Idempotent-Replayed: true`; same key + different body → 409. |
| Correlation | Every response has `X-Request-ID` (caller value reused if `^[A-Za-z0-9._-]{8,64}$`). |
| Errors | `{"error": {"code", "message", "request_id", "details": [{"field", "message"}]}}`; submitted values are never echoed. |
| Pagination | Keyset: `limit` (≤ 200) and `cursor` (opaque) → `next_cursor`. |
| Money | Decimal strings with a `currency`. Different currencies are never summed. |
| Time | UTC; dates are UTC calendar days. Default windows end at the latest complete day (`as_of`). |
| Long work | Imports, analysis, forecasts, investigations, and remediations return `202` and run as jobs (`GET /jobs/{id}`). |
| Limits | Request bodies ≤ 12 MiB, uploads ≤ 10 MiB; login 10/min per IP; 600 requests/min per session or IP (429 + `Retry-After`). |
| Provenance | Responses label `mode`/`data_mode` (`demo`, `live`, `simulation`, model provider); savings are always estimates. |

## Endpoints (generated from the OpenAPI document)

### anomalies

| Method | Path | Summary | Success |
| --- | --- | --- | --- |
| `GET` | `/api/v1/anomalies` | Detected cost anomalies | 200 |
| `GET` | `/api/v1/anomalies/{anomaly_id}` | Anomaly with evidence | 200 |
| `POST` | `/api/v1/anomalies/{anomaly_id}/status` | Change anomaly status | 200 |

### approvals

| Method | Path | Summary | Success |
| --- | --- | --- | --- |
| `GET` | `/api/v1/actions` | Allowlisted remediation actions | 200 |
| `GET` | `/api/v1/approvals` | Approval requests | 200 |
| `POST` | `/api/v1/approvals` | Request approval for an action | 201 |
| `POST` | `/api/v1/approvals/preview` | Policy check and impact preview (creates nothing) | 200 |
| `GET` | `/api/v1/approvals/{approval_id}` | One approval request | 200 |
| `POST` | `/api/v1/approvals/{approval_id}/approve` | Approve (requires the reviewed payload_hash) | 200 |
| `POST` | `/api/v1/approvals/{approval_id}/reject` | Reject with a reason | 200 |
| `GET` | `/api/v1/remediations` | Remediation executions | 200 |
| `POST` | `/api/v1/remediations` | Execute an approved action (simulation) | 202 |
| `GET` | `/api/v1/remediations/{execution_id}` | Execution outcome and verification | 200 |

### audit

| Method | Path | Summary | Success |
| --- | --- | --- | --- |
| `GET` | `/api/v1/audit-events` | Append-only audit trail | 200 |

### budgets

| Method | Path | Summary | Success |
| --- | --- | --- | --- |
| `GET` | `/api/v1/budgets` | Budgets with current-month utilization | 200 |
| `POST` | `/api/v1/budgets` | Create a monthly budget | 201 |
| `GET` | `/api/v1/budgets/alerts` | Threshold alerts | 200 |
| `POST` | `/api/v1/budgets/alerts/{alert_id}/acknowledge` | Acknowledge an alert | 200 |

### connections

| Method | Path | Summary | Success |
| --- | --- | --- | --- |
| `GET` | `/api/v1/connections` | List cloud connections | 200 |
| `POST` | `/api/v1/connections` | Register connection metadata (read-only role reference, no keys) | 201 |
| `POST` | `/api/v1/connections/aws/validate` | Validate a read-only AWS role (no keys are accepted or stored) | 200 |
| `GET` | `/api/v1/connections/{connection_id}` | Get a connection | 200 |
| `POST` | `/api/v1/connections/{connection_id}/sync` | Queue a read-only Cost Explorer import and inventory sync | 202 |

### costs

| Method | Path | Summary | Success |
| --- | --- | --- | --- |
| `GET` | `/api/v1/costs/breakdown` | Cost by one dimension with prior-period change | 200 |
| `GET` | `/api/v1/costs/dimensions` | Filter values present in the data | 200 |
| `GET` | `/api/v1/costs/summary` | Totals, comparisons, allocation, drivers | 200 |
| `GET` | `/api/v1/costs/timeseries` | Cost over time, optionally grouped | 200 |
| `GET` | `/api/v1/costs/unit-costs` | Cost per provider usage unit | 200 |

### forecasts

| Method | Path | Summary | Success |
| --- | --- | --- | --- |
| `GET` | `/api/v1/forecasts` | Latest forecast per scope and horizon | 200 |
| `POST` | `/api/v1/forecasts/run` | Queue a forecast run | 202 |
| `GET` | `/api/v1/forecasts/{forecast_id}` | One forecast run | 200 |

### health

| Method | Path | Summary | Success |
| --- | --- | --- | --- |
| `GET` | `/api/v1/health/live` | Process liveness | 200 |
| `GET` | `/api/v1/health/ready` | Readiness: database reachable and migrations at head | 200 |
| `GET` | `/api/v1/metrics` | Prometheus metrics | 200 |

### identity

| Method | Path | Summary | Success |
| --- | --- | --- | --- |
| `POST` | `/api/v1/auth/login` | Demo password login | 200 |
| `POST` | `/api/v1/auth/logout` | Revoke the current session | 204 |
| `GET` | `/api/v1/me` | Current user, tenant, and permissions | 200 |
| `GET` | `/api/v1/tenants/current` | Tenant bound to the session | 200 |

### imports

| Method | Path | Summary | Success |
| --- | --- | --- | --- |
| `GET` | `/api/v1/imports` | List billing imports | 200 |
| `POST` | `/api/v1/imports/demo` | Import reproducible synthetic demo data (labeled demo, never live) | 202 |
| `POST` | `/api/v1/imports/upload` | Upload a billing CSV (validated per row by a background job) | 202 |
| `GET` | `/api/v1/imports/{import_id}` | Import status and row errors | 200 |

### investigations

| Method | Path | Summary | Success |
| --- | --- | --- | --- |
| `POST` | `/api/v1/anomalies/{anomaly_id}/investigate` | Start an evidence-backed investigation (deterministic unless an LLM is configured) | 202 |
| `GET` | `/api/v1/investigations` | List investigations | 200 |
| `GET` | `/api/v1/investigations/{investigation_id}` | Investigation with steps | 200 |
| `POST` | `/api/v1/investigations/{investigation_id}/cancel` | Request cancellation | 200 |
| `GET` | `/api/v1/investigations/{investigation_id}/report.md` | Replayable Markdown report | 200 |

### jobs

| Method | Path | Summary | Success |
| --- | --- | --- | --- |
| `GET` | `/api/v1/jobs/{job_id}` | Background job status | 200 |

### recommendations

| Method | Path | Summary | Success |
| --- | --- | --- | --- |
| `POST` | `/api/v1/analysis/run` | Queue a full analysis refresh | 202 |
| `GET` | `/api/v1/recommendations` | List recommendations | 200 |
| `GET` | `/api/v1/recommendations/{rec_id}` | Recommendation with evidence | 200 |
| `POST` | `/api/v1/recommendations/{rec_id}/dismiss` | Dismiss with a reason | 200 |
| `POST` | `/api/v1/recommendations/{rec_id}/simulate` | Estimate the cost effect of applying a recommendation (changes nothing) | 200 |

### resources

| Method | Path | Summary | Success |
| --- | --- | --- | --- |
| `GET` | `/api/v1/resources` | List inventory | 200 |
| `GET` | `/api/v1/resources/{resource_uuid}` | Resource with recent cost | 200 |

