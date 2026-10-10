"""Evidence-backed recommendations and deterministic simulation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from nimbrix_api.api.v1.jobs import job_out
from nimbrix_api.api.v1.paths import API_V1_PREFIX
from nimbrix_api.core.audit import record_audit
from nimbrix_api.core.deps import AuthContext, DbSession, require
from nimbrix_api.core.errors import ApiError
from nimbrix_api.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, paginate
from nimbrix_api.core.permissions import Permission
from nimbrix_api.core.tenancy import get_owned
from nimbrix_api.models import Recommendation
from nimbrix_api.modules.ingestion.schemas import JobOut
from nimbrix_api.modules.jobs import queue
from nimbrix_api.modules.recommendations import service
from nimbrix_api.modules.recommendations.schemas import (
    DismissRequest,
    RecommendationList,
    RecommendationOut,
    SimulationOut,
)

router = APIRouter(prefix=API_V1_PREFIX, tags=["recommendations"])
Reader = Annotated[AuthContext, Depends(require(Permission.READ))]


def rec_out(rec: Recommendation) -> RecommendationOut:
    return RecommendationOut.model_validate(rec, from_attributes=True)


@router.get("/recommendations", response_model=RecommendationList, summary="List recommendations")
def list_recommendations(
    auth: Reader,
    session: DbSession,
    status: Annotated[str | None, Query(max_length=32)] = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
    include_superseded: bool = False,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> RecommendationList:
    stmt = select(Recommendation).where(Recommendation.tenant_id == auth.tenant_id)
    if status:
        stmt = stmt.where(Recommendation.status == status)
    elif not include_superseded:
        stmt = stmt.where(Recommendation.status != "superseded")
    if category:
        stmt = stmt.where(Recommendation.category == category)
    rows, next_cursor = paginate(
        session, stmt, Recommendation.created_at, Recommendation.id, limit, cursor
    )
    open_savings = session.scalars(
        select(Recommendation.estimated_monthly_savings).where(
            Recommendation.tenant_id == auth.tenant_id,
            Recommendation.status == "open",
            Recommendation.currency == "USD",
        )
    ).all()
    items = sorted(
        (rec_out(r) for r in rows),
        key=lambda r: r.estimated_monthly_savings or Decimal(0),
        reverse=True,
    )
    return RecommendationList(
        items=items,
        total_estimated_monthly_savings=sum((s for s in open_savings if s is not None), Decimal(0)),
        currency="USD",
        next_cursor=next_cursor,
    )


@router.get(
    "/recommendations/{rec_id}",
    response_model=RecommendationOut,
    summary="Recommendation with evidence",
)
def get_recommendation(rec_id: uuid.UUID, auth: Reader, session: DbSession) -> RecommendationOut:
    return rec_out(get_owned(session, Recommendation, auth, rec_id, label="recommendation"))


@router.post(
    "/recommendations/{rec_id}/simulate",
    response_model=SimulationOut,
    summary="Estimate the cost effect of applying a recommendation (changes nothing)",
)
def simulate_recommendation(
    rec_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(require(Permission.RECOMMENDATIONS_SIMULATE))],
    session: DbSession,
) -> SimulationOut:
    rec = get_owned(session, Recommendation, auth, rec_id, label="recommendation", for_update=True)
    result = service.simulate(session, rec)
    rec.last_simulation = result
    record_audit(
        session,
        event_type="recommendation.simulated",
        tenant_id=auth.tenant_id,
        actor_id=auth.user_id,
        actor_label=auth.email,
        target_type="recommendation",
        target_id=rec.id,
        payload={
            "status": result["status"],
            "estimated_monthly_savings": result.get("estimated_monthly_savings"),
        },
    )
    session.commit()
    return SimulationOut.model_validate(result)


@router.post(
    "/recommendations/{rec_id}/dismiss",
    response_model=RecommendationOut,
    summary="Dismiss with a reason",
)
def dismiss_recommendation(
    rec_id: uuid.UUID,
    body: DismissRequest,
    auth: Annotated[AuthContext, Depends(require(Permission.RECOMMENDATIONS_UPDATE))],
    session: DbSession,
) -> RecommendationOut:
    rec = get_owned(session, Recommendation, auth, rec_id, label="recommendation", for_update=True)
    if rec.status in ("approval_requested", "actioned"):
        raise ApiError(
            409, "invalid_state", f"Cannot dismiss a recommendation in state '{rec.status}'"
        )
    rec.status = "dismissed"
    record_audit(
        session,
        event_type="recommendation.dismissed",
        tenant_id=auth.tenant_id,
        actor_id=auth.user_id,
        actor_label=auth.email,
        target_type="recommendation",
        target_id=rec.id,
        payload={"reason": body.reason},
    )
    session.commit()
    return rec_out(rec)


@router.post(
    "/analysis/run", response_model=JobOut, status_code=202, summary="Queue a full analysis refresh"
)
def run_analysis(
    auth: Annotated[AuthContext, Depends(require(Permission.ANALYSIS_RUN))],
    session: DbSession,
) -> JobOut:
    job = queue.enqueue(
        session,
        tenant_id=auth.tenant_id,
        kind="analyze_tenant",
        payload={"reason": "manual", "requested_at": datetime.now(UTC).isoformat()},
        created_by=auth.user_id,
        dedupe_key=f"analyze:{auth.tenant_id}",
    )
    record_audit(
        session,
        event_type="analysis.requested",
        tenant_id=auth.tenant_id,
        actor_id=auth.user_id,
        actor_label=auth.email,
        target_type="job",
        target_id=job.id,
    )
    session.commit()
    return job_out(job)
