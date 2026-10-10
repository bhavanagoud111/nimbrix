"""Read-only, tenant-scoped evidence tools for investigation agents.

Every query filters by the investigation's tenant. Tools run in a database transaction
opened with ``SET TRANSACTION READ ONLY``, so even a bug cannot write. No tool reaches a
cloud provider, and there is no write or approval tool at all.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from nimbrix_api.models import Anomaly, Forecast, Recommendation, ResourceInventory
from nimbrix_api.modules.agents.schemas import Evidence
from nimbrix_api.modules.costs import service as costs
from nimbrix_api.modules.pricing import PRICE_SOURCE


class ToolAccessDeniedError(Exception):
    """A tool was asked for data outside the investigation's tenant."""


@contextmanager
def read_only_session(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            yield session
        finally:
            session.rollback()


class EvidenceTools:
    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.calls: list[dict[str, Any]] = []

    def _log(self, tool: str, **args: Any) -> None:
        self.calls.append({"tool": tool, "args": {k: str(v) for k, v in args.items()}})

    def anomaly_context(self, anomaly_id: uuid.UUID) -> tuple[Anomaly, list[Evidence]]:
        self._log("anomaly_context", anomaly_id=anomaly_id)
        anomaly = self.session.scalar(
            select(Anomaly).where(Anomaly.id == anomaly_id, Anomaly.tenant_id == self.tenant_id)
        )
        if anomaly is None:
            raise ToolAccessDeniedError("anomaly is not visible to this tenant")
        evidence = [
            Evidence(
                id=f"anomaly:{anomaly.id}",
                kind="anomaly",
                summary=(
                    f"{anomaly.scope_type} {anomaly.scope_value}: observed {anomaly.observed} vs expected "
                    f"{anomaly.expected} {anomaly.currency} over {anomaly.window_start}..{anomaly.window_end} "
                    f"(score {anomaly.score}, {anomaly.severity})"
                ),
                data={
                    "scope_type": anomaly.scope_type,
                    "scope_value": anomaly.scope_value,
                    "window_start": str(anomaly.window_start),
                    "window_end": str(anomaly.window_end),
                    "observed": str(anomaly.observed),
                    "expected": str(anomaly.expected),
                    "currency": anomaly.currency,
                    "severity": anomaly.severity,
                    "detector_version": anomaly.detector_version,
                },
            )
        ]
        for day in anomaly.evidence.get("daily", []):
            evidence.append(
                Evidence(
                    id=day["evidence_id"],
                    kind="daily_cost",
                    summary=f"{day['date']}: observed {day['observed']} vs expected {day['expected']} (score {day['score']})",
                    data=day,
                )
            )
        return anomaly, evidence

    def contributors(self, anomaly: Anomaly) -> list[dict[str, Any]]:
        self._log("contributors", anomaly_id=anomaly.id)
        return list(anomaly.evidence.get("contributors", []))

    def resource_inventory(self, resource_ids: list[str]) -> list[Evidence]:
        self._log("resource_inventory", resource_ids=",".join(resource_ids))
        if not resource_ids:
            return []
        rows = self.session.scalars(
            select(ResourceInventory).where(
                ResourceInventory.tenant_id == self.tenant_id,
                ResourceInventory.resource_id.in_(resource_ids),
            )
        ).all()
        return [
            Evidence(
                id=f"resource:{r.resource_id}",
                kind="resource",
                summary=(
                    f"{r.resource_type} {r.resource_id} ({r.attributes.get('instance_type') or r.attributes.get('volume_type') or ''}) "
                    f"env={r.environment} team={r.team} state={r.state} account={r.account_id} region={r.region}"
                ),
                data={
                    "name": r.name,
                    "resource_type": r.resource_type,
                    "account_id": r.account_id,
                    "region": r.region,
                    "environment": r.environment,
                    "team": r.team,
                    "state": r.state,
                    "launch_time": r.attributes.get("launch_time"),
                    "instance_type": r.attributes.get("instance_type"),
                    "hourly_cost": str(r.hourly_cost) if r.hourly_cost is not None else None,
                    "cpu_avg_pct": (r.utilization or {}).get("statistic_avg_pct"),
                    "observed_at": r.observed_at.isoformat(),
                },
            )
            for r in rows
        ]

    def resource_uuid(self, resource_uuid: uuid.UUID) -> ResourceInventory:
        """Lookup by internal ID; used to prove cross-tenant IDs are refused."""
        self._log("resource_uuid", resource_uuid=resource_uuid)
        row = self.session.scalar(
            select(ResourceInventory).where(
                ResourceInventory.id == resource_uuid, ResourceInventory.tenant_id == self.tenant_id
            )
        )
        if row is None:
            raise ToolAccessDeniedError("resource is not visible to this tenant")
        return row

    def scope_cost_baseline(self, anomaly: Anomaly) -> Evidence:
        self._log("scope_cost_baseline", anomaly_id=anomaly.id)
        start = anomaly.window_start - timedelta(days=28)
        end = anomaly.window_start - timedelta(days=1)
        daily = costs.daily_totals(
            self.session,
            self.tenant_id,
            scope_type=anomaly.scope_type,
            scope_value=None if anomaly.scope_type == "total" else anomaly.scope_value,
            currency=anomaly.currency,
            start=start,
            end=end,
        )
        total = sum(daily.values(), Decimal(0))
        average = (total / 28).quantize(Decimal("0.01"))
        return Evidence(
            id=f"cost-baseline:{anomaly.scope_type}:{anomaly.scope_value}:{start}:{end}",
            kind="cost_baseline",
            summary=f"28-day average before the anomaly: {average} {anomaly.currency}/day ({start}..{end})",
            data={
                "average_daily": str(average),
                "start": str(start),
                "end": str(end),
                "days_with_data": len(daily),
            },
        )

    def forecast_context(self, anomaly: Anomaly) -> Evidence | None:
        self._log("forecast_context", scope=f"{anomaly.scope_type}:{anomaly.scope_value}")
        forecast = self.session.scalar(
            select(Forecast)
            .where(
                Forecast.tenant_id == self.tenant_id,
                Forecast.scope_type == anomaly.scope_type,
                Forecast.scope_value == anomaly.scope_value,
                Forecast.status == "succeeded",
            )
            .order_by(Forecast.created_at.desc())
            .limit(1)
        )
        if forecast is None or not forecast.points:
            return None
        horizon_total = sum((Decimal(p["yhat"]) for p in forecast.points), Decimal(0)).quantize(
            Decimal("0.01")
        )
        return Evidence(
            id=f"forecast:{forecast.id}",
            kind="forecast",
            summary=(
                f"{forecast.model_name} forecast trained through {forecast.training_end}: "
                f"{horizon_total} {forecast.currency} over the next {forecast.horizon_days} days"
            ),
            data={
                "model": forecast.model_name,
                "model_version": forecast.model_version,
                "training_end": str(forecast.training_end),
                "horizon_days": forecast.horizon_days,
                "horizon_total": str(horizon_total),
                "selected_model_metrics": forecast.metrics.get("models", {}).get(
                    forecast.model_name
                ),
            },
        )

    def previous_incidents(self, anomaly: Anomaly) -> list[Evidence]:
        self._log("previous_incidents", scope=f"{anomaly.scope_type}:{anomaly.scope_value}")
        rows = self.session.scalars(
            select(Anomaly)
            .where(
                Anomaly.tenant_id == self.tenant_id,
                Anomaly.scope_type == anomaly.scope_type,
                Anomaly.scope_value == anomaly.scope_value,
                Anomaly.id != anomaly.id,
                Anomaly.window_start < anomaly.window_start,
            )
            .order_by(Anomaly.window_start.desc())
            .limit(3)
        ).all()
        return [
            Evidence(
                id=f"anomaly:{a.id}",
                kind="previous_incident",
                summary=f"Earlier anomaly {a.window_start}..{a.window_end} ({a.status})",
                data={"status": a.status, "severity": a.severity},
            )
            for a in rows
        ]

    def recommendations_for(
        self, resource_ids: list[str], account_id: str | None, team: str | None
    ) -> list[Evidence]:
        self._log("recommendations_for", resources=len(resource_ids), account=account_id, team=team)
        stmt = select(Recommendation).where(
            Recommendation.tenant_id == self.tenant_id,
            Recommendation.status.in_(("open", "insufficient_evidence")),
        )
        rows = self.session.scalars(stmt).all()
        related = [
            r
            for r in rows
            if r.resource_id in resource_ids
            or (account_id and r.account_id == account_id)
            or (team and r.owner == team)
        ]
        return [
            Evidence(
                id=f"recommendation:{r.id}",
                kind="recommendation",
                summary=f"{r.title} (est. {r.estimated_monthly_savings} {r.currency}/month, {r.confidence} confidence)",
                data={
                    "category": r.category,
                    "resource_id": r.resource_id,
                    "estimated_monthly_savings": str(r.estimated_monthly_savings)
                    if r.estimated_monthly_savings is not None
                    else None,
                    "proposed_action": r.proposed_action,
                    "status": r.status,
                    "account_id": r.account_id,
                    "region": r.region,
                },
            )
            for r in related[:10]
        ]

    def pricing_assumptions(self) -> Evidence:
        self._log("pricing_assumptions")
        return Evidence(
            id="pricing:reference-v1",
            kind="pricing",
            summary=PRICE_SOURCE,
            data={"source": PRICE_SOURCE},
        )
