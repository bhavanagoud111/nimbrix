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
