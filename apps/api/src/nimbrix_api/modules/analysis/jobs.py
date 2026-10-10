"""The analysis pipeline job: deterministic rules and budgets (ML stages are added in phase 4)."""

from __future__ import annotations

from typing import Any

from nimbrix_api.core.audit import record_audit
from nimbrix_api.modules.budgets import service as budgets
from nimbrix_api.modules.jobs.runner import JobContext, register
from nimbrix_api.modules.recommendations import service as recommendations


@register("analyze_tenant")
def analyze_tenant(ctx: JobContext) -> dict[str, Any]:
    result: dict[str, Any] = {}
    with ctx.session_factory() as session:
        result["recommendations"] = recommendations.refresh(session, ctx.tenant_id)
        session.commit()
        ctx.heartbeat()
        result["budget_alerts_created"] = budgets.evaluate_alerts(session, ctx.tenant_id)
        record_audit(
            session,
            event_type="analysis.completed",
            tenant_id=ctx.tenant_id,
            actor_type="system",
            target_type="job",
            target_id=ctx.job_id,
            payload={
                "recommendations": result["recommendations"],
                "budget_alerts": result["budget_alerts_created"],
            },
        )
        session.commit()
    return result
