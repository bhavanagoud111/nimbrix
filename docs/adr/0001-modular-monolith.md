# ADR-0001: Modular monolith API plus separate worker process

- Status: Accepted
- Date: 2026-10-10

## Context

Nimbrix has many domains (ingestion, analytics, ML, agents, approvals, remediation,
audit) but one team, one database, and a local-first demo requirement. Domains share
strong invariants — tenant scoping, audit writes, approval state — that are easiest to
enforce inside one transaction boundary.

## Decision

Build one FastAPI application (`apps/api`) organized into domain modules with explicit
internal interfaces (router → service → repository). Long-running work runs in a
separate **worker process** started from the same Python package and image, so both
share models, validation, and authorization code.

Module rules:

- Routers do HTTP concerns only; services hold business rules; repositories hold queries.
- Cross-module calls go through service interfaces, never another module's tables directly.
- Every service method takes the caller's tenant context explicitly (Phase 1).

## Alternatives considered

- **Microservices per domain** — independent scaling we do not need, plus distributed
  transactions for approval/audit consistency, service discovery, and more failure modes.
  Rejected as overengineering (global rule 6).
- **Single process with in-request background tasks** — long jobs would block API
  workers and lose work on restart. Rejected.

## Consequences

- Simple local run: four containers (postgres, migrate, api, web; worker added in Phase 2).
- Module boundaries must be policed by review and tests rather than network isolation.
- If a module later needs independent scaling (e.g., ML training), it can be extracted
  along its service interface.
