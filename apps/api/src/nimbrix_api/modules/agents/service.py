"""Investigation lifecycle: request, execute (as a job), persist, export."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from nimbrix_api.core.audit import record_audit
from nimbrix_api.core.config import Settings
from nimbrix_api.models import Investigation
from nimbrix_api.modules.agents.graph import (
    BudgetExceededError,
    InvestigationCancelledError,
    Runtime,
    build_graph,
)
from nimbrix_api.modules.agents.providers import InvestigationModel, build_model
from nimbrix_api.modules.agents.tools import EvidenceTools, read_only_session
from nimbrix_api.modules.jobs import queue

logger = logging.getLogger(__name__)

# Tests inject fake providers here; production uses settings.
model_factory = build_model


def configured_mode(settings: Settings) -> tuple[str, str | None]:
    model = model_factory(settings)
    return (model.provider, model.model) if model else ("deterministic", None)


def request_investigation(
    session: Session,
    settings: Settings,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    anomaly_id: uuid.UUID,
    question: str | None,
) -> Investigation:
    mode, model = configured_mode(settings)
    investigation = Investigation(
        tenant_id=tenant_id,
        anomaly_id=anomaly_id,
        state="queued",
        mode=mode,
        model=model,
        trace_id=uuid.uuid4().hex,
        question=question or "What caused this cost anomaly, and what can be done about it?",
        evidence=[],
        budget={
            "max_steps": settings.agent_max_steps,
            "timeout_seconds": settings.agent_timeout_seconds,
            "max_llm_calls": settings.agent_max_llm_calls,
        },
        usage={},
        requested_by=user_id,
    )
    session.add(investigation)
    session.flush()
    queue.enqueue(
        session,
        tenant_id=tenant_id,
        kind="investigate",
        payload={"investigation_id": str(investigation.id)},
        created_by=user_id,
        dedupe_key=f"investigate:{investigation.id}",
        max_attempts=1,
    )
    return investigation


def execute(
    factory: sessionmaker[Session],
    settings: Settings,
    tenant_id: uuid.UUID,
    investigation_id: uuid.UUID,
    model: InvestigationModel | None = None,
) -> dict[str, Any]:
    with factory() as session:
        inv = session.scalar(
            select(Investigation).where(
                Investigation.id == investigation_id, Investigation.tenant_id == tenant_id
            )
        )
        if inv is None:
            return {"state": "missing"}
        if inv.cancel_requested:
            inv.state, inv.finished_at = "cancelled", datetime.now(UTC)
            session.commit()
            return {"state": "cancelled"}
        inv.state, inv.started_at = "running", datetime.now(UTC)
        question, anomaly_id = inv.question or "", inv.anomaly_id
        session.commit()

    model = model if model is not None else model_factory(settings)
    with read_only_session(factory) as ro:
        runtime = Runtime(
            investigation_id=investigation_id,
            tenant_id=tenant_id,
            settings=settings,
            tools=EvidenceTools(ro, tenant_id),
            write_factory=factory,
            model=model,
            deadline=time.monotonic() + settings.agent_timeout_seconds,
        )
        graph = build_graph(runtime)
        state: str
        error: str | None = None
        final: dict[str, Any] = {}
        try:
            final = graph.invoke(
                {
                    "question": question,
                    "anomaly_id": str(anomaly_id) if anomaly_id else None,
                    "evidence": [],
                },
                config={"recursion_limit": settings.agent_max_steps + 2},
            )
            state = "completed"
        except InvestigationCancelledError:
            state = "cancelled"
        except BudgetExceededError as exc:
            state, error = "failed", str(exc)
        except Exception as exc:  # recorded on the investigation, then surfaced by the job
            logger.exception(
                "investigation_failed", extra={"investigation_id": str(investigation_id)}
            )
            state, error = "failed", f"internal error ({type(exc).__name__})"

    with factory() as session:
        inv = session.get(Investigation, investigation_id)
        assert inv is not None  # noqa: S101
        report = final.get("report")
        inv.state = state
        inv.error = error
        inv.report = report
        if report:
            inv.mode = report["mode"]
            inv.model = report["model"]
            inv.evidence = report["evidence"]
        inv.usage = {
            **runtime.usage,
            "steps": runtime.steps,
            "tool_calls": len(runtime.tools.calls),
        }
        inv.finished_at = datetime.now(UTC)
        record_audit(
            session,
            event_type=f"investigation.{state}",
            tenant_id=tenant_id,
            actor_type="agent",
            target_type="investigation",
            target_id=investigation_id,
            outcome="success" if state == "completed" else "failure",
            payload={
                "mode": inv.mode,
                "steps": runtime.steps,
                "error": error,
                "trace_id": inv.trace_id,
            },
        )
        session.commit()
    return {"state": state, "steps": runtime.steps, "error": error}


def render_markdown(inv: Investigation) -> str:
    report = inv.report or {}
    lines = [
        f"# {report.get('title', 'Investigation')}",
        "",
        f"- Investigation: `{inv.id}` (trace `{inv.trace_id}`)",
        f"- Mode: **{report.get('mode', inv.mode)}**"
        + (f" (model `{report['model']}`)" if report.get("model") else ""),
        f"- State: {inv.state}; created {inv.created_at.isoformat()}",
        "",
        "## Summary",
        "",
        report.get("summary", inv.error or "No report."),
        "",
    ]
    if report.get("hypotheses"):
        lines += ["## Hypotheses", ""]
        for h in report["hypotheses"]:
            cites = ", ".join(f"`{i}`" for i in h["evidence_ids"])
            lines.append(f"- ({h['confidence']}) {h['statement']} — evidence: {cites}")
        lines.append("")
    if report.get("key_figures"):
        lines += ["## Key figures (computed, not model-generated)", ""]
        lines += [f"- {k}: {v}" for k, v in report["key_figures"].items()]
        lines.append("")
    if report.get("proposed_actions"):
        lines += ["## Proposed actions (policy-reviewed, not executed)", ""]
        for a in report["proposed_actions"]:
            verdict = (
                "allowed"
                if a["policy_allowed"]
                else "denied: " + "; ".join(a["policy_denial_reasons"])
            )
            lines.append(
                f"- `{a['action_type']}` on `{a['resource_id']}` — est. {a['estimated_monthly_savings']}/month — {verdict}"
            )
        lines.append("")
    if report.get("missing_evidence"):
        lines += ["## Missing evidence", ""] + [f"- {m}" for m in report["missing_evidence"]] + [""]
    if report.get("model_output_rejected"):
        lines += (
            ["## Rejected model output", ""]
            + [f"- {r}" for r in report["model_output_rejected"]]
            + [""]
        )
    lines += ["## Evidence", ""]
    lines += [f"- `{e['id']}` ({e['kind']}): {e['summary']}" for e in report.get("evidence", [])]
    lines += ["", "## Limitations", ""] + [f"- {item}" for item in report.get("limitations", [])]
    return "\n".join(lines) + "\n"
