"""Spend forecasts with uncertainty and validation metrics."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on

from nimbrix_api.api.v1.jobs import job_out
from nimbrix_api.api.v1.paths import API_V1_PREFIX
from nimbrix_api.core.audit import record_audit
from nimbrix_api.core.deps import AuthContext, DbSession, require
from nimbrix_api.core.permissions import Permission
from nimbrix_api.core.tenancy import get_owned
from nimbrix_api.models import Forecast
from nimbrix_api.modules.ingestion.schemas import JobOut
from nimbrix_api.modules.jobs import queue
from nimbrix_api.modules.ml.schemas import ForecastList, ForecastOut, ForecastRunRequest

router = APIRouter(prefix=API_V1_PREFIX, tags=["forecasts"])
Reader = Annotated[AuthContext, Depends(require(Permission.READ))]


def forecast_out(f: Forecast) -> ForecastOut:
    return ForecastOut.model_validate(f, from_attributes=True)


@router.get(
    "/forecasts", response_model=ForecastList, summary="Latest forecast per scope and horizon"
)
def list_forecasts(
    auth: Reader,
    session: DbSession,
    scope_type: Annotated[str | None, Query(max_length=32)] = None,
    scope_value: Annotated[str | None, Query(max_length=256)] = None,
    horizon_days: Annotated[int | None, Query(ge=1, le=90)] = None,
) -> ForecastList:
    stmt = (
        select(Forecast)
        .where(Forecast.tenant_id == auth.tenant_id)
        .ext(distinct_on(Forecast.scope_type, Forecast.scope_value, Forecast.horizon_days))
        .order_by(
            Forecast.scope_type,
            Forecast.scope_value,
            Forecast.horizon_days,
            Forecast.created_at.desc(),
        )
    )
    if scope_type:
        stmt = stmt.where(Forecast.scope_type == scope_type)
    if scope_value:
        stmt = stmt.where(Forecast.scope_value == scope_value)
    if horizon_days:
        stmt = stmt.where(Forecast.horizon_days == horizon_days)
    return ForecastList(items=[forecast_out(f) for f in session.scalars(stmt.limit(100))])


@router.get("/forecasts/{forecast_id}", response_model=ForecastOut, summary="One forecast run")
def get_forecast(forecast_id: uuid.UUID, auth: Reader, session: DbSession) -> ForecastOut:
    return forecast_out(get_owned(session, Forecast, auth, forecast_id, label="forecast"))


@router.post(
    "/forecasts/run", response_model=JobOut, status_code=202, summary="Queue a forecast run"
)
def run_forecast(
    body: ForecastRunRequest,
    auth: Annotated[AuthContext, Depends(require(Permission.ANALYSIS_RUN))],
    session: DbSession,
) -> JobOut:
    payload = body.model_dump(mode="json")
    if body.scope_type == "total":
        payload["scope_value"] = "total"
    job = queue.enqueue(
        session,
        tenant_id=auth.tenant_id,
        kind="forecast_run",
        payload=payload,
        created_by=auth.user_id,
        dedupe_key=f"forecast:{payload['scope_type']}:{payload['scope_value']}:{body.horizon_days}",
    )
    record_audit(
        session,
        event_type="forecast.requested",
        tenant_id=auth.tenant_id,
        actor_id=auth.user_id,
        actor_label=auth.email,
        target_type="job",
        target_id=job.id,
        payload=payload,
    )
    session.commit()
    return job_out(job)
