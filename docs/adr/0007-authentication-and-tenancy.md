# ADR-0007: Demo authentication, RBAC, and server-side tenancy

- Status: Accepted
- Date: 2026-10-10

## Context

Nimbrix is multi-tenant and must enforce roles server-side. The local demo cannot
depend on an external identity provider, but demo auth must not masquerade as
production-grade identity.

## Decision

- **Demo authentication** (`POST /api/v1/auth/login`): email + password (scrypt hashes,
  stdlib), opaque 256-bit session tokens. Only a SHA-256 hash of the token is stored in
  `sessions`; sessions expire (`NIMBRIX_SESSION_TTL_HOURS`) and are revocable (logout).
- **Transport:** `nimbrix_session` cookie (HttpOnly, SameSite=Lax, Secure in production).
  Bearer transport of the same token is accepted for scripts and tests.
- **CSRF:** cookie-authenticated mutations require `X-CSRF-Token`, a per-session random
  token whose hash is bound to the session (double-submit validated server-side).
  Bearer requests are exempt (not ambient credentials).
- **Tenancy:** the tenant is fixed by the session at login. No endpoint accepts a tenant ID
  from the client. Tenant-owned lookups use `get_owned()`, which returns 404 for rows in
  other tenants and writes a `security.cross_tenant_access_denied` audit event.
- **RBAC:** five roles — viewer, analyst, approver, auditor, admin. The permission matrix
  lives in code (`core/permissions.py`) and is mirrored into the `roles` table by the seed.
  Every endpoint declares its permission with `require(...)`; denials are audited.
- **Production identity:** OIDC (authorization code + PKCE) at the web tier, with the API
  validating ID/access tokens (issuer, audience, signature via JWKS, expiry) and mapping
  a groups claim to roles. **Not implemented.** Startup fails in `production` while demo
  auth is enabled unless `NIMBRIX_ALLOW_DEMO_AUTH_IN_PRODUCTION=true` explicitly
  acknowledges the risk.
- **Idempotency:** every mutation requires `Idempotency-Key`; responses are stored for 24 h
  per (tenant, user, key). Reusing a key with a different body returns 409.

## Consequences

- The web proxy forwards only `nimbrix_*` cookies and `X-CSRF-Token` (supersedes the
  header list in ADR-0006).
- Login itself is rate-limited (Phase 8) and audited (success and failure).
