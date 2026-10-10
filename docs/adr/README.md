# Architecture Decision Records

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-modular-monolith.md) | Modular monolith API plus separate worker process | Accepted |
| [0002](0002-postgresql-storage.md) | PostgreSQL 16 as the single system of record | Accepted |
| [0003](0003-db-backed-jobs.md) | Database-backed background jobs (no Celery/Redis initially) | Accepted |
| [0004](0004-model-provider.md) | Provider-neutral LLM interface with deterministic fallback | Accepted |
| [0005](0005-safety-policy.md) | Remediation safety policy: simulation first, human approval always | Accepted |
| [0006](0006-same-origin-api-proxy.md) | Browser reaches the API only through a same-origin proxy | Accepted (header list amended by 0007) |
| [0007](0007-authentication-and-tenancy.md) | Demo authentication, RBAC, and server-side tenancy | Accepted |

New ADRs: copy the structure of an existing one (Context, Decision, Alternatives,
Consequences), number sequentially, and never rewrite an accepted ADR — supersede it.
