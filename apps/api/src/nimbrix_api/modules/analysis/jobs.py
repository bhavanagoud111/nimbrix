"""The analysis pipeline: anomalies -> forecasts -> recommendations -> budget alerts."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from nimbrix_api.core.audit import record_audit
from nimbrix_api.models import Budget
from nimbrix_api.modules.budgets import service as budgets
from nimbrix_api.modules.jobs.runner import JobContext, register
from nimbrix_api.modules.ml import anomaly_service, forecasting
from nimbrix_api.modules.recommendations import service as recommendations


@register("analyze_tenant")
def analyze_tenant(ctx: JobContext) -> dict[str, Any]:
    sensitivity = ctx.payload.get("sensitivity", "medium")
    result: dict[str, Any] = {}
    with ctx.session_factory() as session:
        result["anomalies"] = anomaly_service.detect_for_tenant(
            session, ctx.tenant_id, sensitivity=sensitivity
        )
        session.commit()
        ctx.heartbeat()

        scopes = {("total", "total")}
        for budget in session.scalars(select(Budget).where(Budget.tenant_id == ctx.tenant_id)):
            if budget.scope_type != "total" and budget.scope_value:
                scopes.add((budget.scope_type, budget.scope_value))
        forecasts = {"computed": 0, "reused": 0}
        for scope_type, scope_value in sorted(scopes):
            _, computed = forecasting.ensure_forecast(
                session, ctx.tenant_id, scope_type=scope_type, scope_value=scope_value, horizon=30
            )
            forecasts["computed" if computed else "reused"] += 1
            session.commit()
            ctx.heartbeat()
        result["forecasts"] = forecasts

        result["recommendations"] = recommendations.refresh(session, ctx.tenant_id)
        session.commit()
        result["budget_alerts_created"] = budgets.evaluate_alerts(session, ctx.tenant_id)
        record_audit(
            session,
            event_type="analysis.completed",
            tenant_id=ctx.tenant_id,
            actor_type="system",
            target_type="job",
            target_id=ctx.job_id,
            payload=result,
        )
        session.commit()
    return result


@register("forecast_run")
def forecast_run(ctx: JobContext) -> dict[str, Any]:
    with ctx.session_factory() as session:
        forecast = forecasting.run_forecast(
            session,
            ctx.tenant_id,
            scope_type=ctx.payload["scope_type"],
            scope_value=ctx.payload["scope_value"],
            horizon=int(ctx.payload["horizon_days"]),
            currency=ctx.payload.get("currency", "USD"),
            level=float(ctx.payload.get("interval_level", 0.8)),
        )
        session.commit()
        return {
            "forecast_id": str(forecast.id),
            "status": forecast.status,
            "model": forecast.model_name,
        }
