# ADR-0004: Provider-neutral LLM interface with deterministic fallback

- Status: Accepted
- Date: 2026-10-10

## Context

Investigations benefit from natural-language explanation, but the demo must work with
no API key, results must be reproducible in tests, and LLMs must never be the authority
for dollar amounts or action authorization (global rule 7).

## Decision

- Define a narrow internal interface, `InvestigationModel`, that accepts a typed,
  redacted evidence bundle and returns a **Pydantic-validated structured result**
  (hypotheses citing evidence IDs, confidence label, `insufficient_evidence` flag).
- Adapters: **deterministic** (default; rule-based, no network), and optional
  OpenAI, Anthropic, and Ollama adapters enabled only by explicit configuration.
- LangGraph orchestrates the investigation graph; nodes call the interface, never a
  vendor SDK directly.
- Every investigation records which mode produced it (`deterministic` or provider/model),
  token usage, and cost where available, and the UI displays the mode.
- Agents get **read-only, tenant-scoped tools** only. No tool can call cloud write APIs or
  create approvals.

## Alternatives considered

- **Single vendor SDK** — simpler, but locks the demo to a paid key. Rejected.
- **LLM computes savings/forecasts** — non-reproducible and unverifiable. Rejected.

## Consequences

- Offline demo and CI are deterministic.
- Malformed model output is a handled error path (validated, retried within a bounded
  budget, then reported) rather than a crash.
- Provider keys live only in server-side secret storage; never in the browser or prompts.
