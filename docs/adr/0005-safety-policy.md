# ADR-0005: Remediation safety policy — simulation first, human approval always

- Status: Accepted
- Date: 2026-10-10

## Context

Nimbrix proposes changes to cloud resources. A wrong action (stopping a production
instance) has real cost and availability impact. Recommendations may be influenced by
untrusted inputs (tags, resource names, LLM text).

## Decision

1. **Action registry with an allowlist.** The only registered action is
   `simulate_stop_nonprod_instance`. Unknown actions are rejected server-side.
2. **Simulation is the only execution mode.** `NIMBRIX_LIVE_ACTIONS_ENABLED` exists only
   so that enabling it fails loudly: setting it to `true` is rejected at startup until a
   live adapter is built, reviewed, and explicitly authorized.
3. **Human approval is mandatory** for every execution, including simulations:
   request → policy validation → impact preview → approval → execution → verification → audit.
4. **Integrity controls** (Phase 6): payload hash bound to the approval; approval
   expiry; separation of duties (requester ≠ approver) where configured; tenant
   ownership check; idempotency key per execution; resource state revalidated
   immediately before execution; rollback plan required.
5. **Any future live action** requires: its own feature flag, a narrowly scoped IAM role,
   a resource allowlist, dry-run first, documented blast radius and rollback, and
   explicit user approval to enable — never enabled automatically.
6. **Agents cannot act.** Agent tools are read-only; agents may draft a recommendation
   but cannot create or approve an approval request.
7. **Audit everything.** Approval and execution transitions write append-only audit events
   with actor, target, request ID, and a redacted payload.

## Alternatives considered

- **Auto-remediation above a confidence threshold** — violates the product promise and
  the human-authorization requirement. Rejected.

## Consequences

- The demo's "before/after" savings are always labeled **simulated/estimated**, never realized.
- Additional friction for users is intentional and is the core safety feature.
