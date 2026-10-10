"""Agent investigations of cost anomalies (read-only, evidence-cited)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select

from nimbrix_api.api.v1.paths import API_V1_PREFIX
from nimbrix_api.core.audit import record_audit
from nimbrix_api.core.deps import AppSettings, AuthContext, DbSession, IdempotencyKey, require
from nimbrix_api.core.errors import ApiError
from nimbrix_api.core.idempotency import IdempotencyGuard
from nimbrix_api.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, paginate
from nimbrix_api.core.permissions import Permission
from nimbrix_api.core.tenancy import get_owned
from nimbrix_api.models import AgentRun, Anomaly, Investigation
from nimbrix_api.modules.agents import service
from nimbrix_api.modules.agents.api_schemas import (
    AgentStepOut,
    InvestigateRequest,
    InvestigationList,
    InvestigationOut,
)

router = APIRouter(prefix=API_V1_PREFIX, tags=["investigations"])
Reader = Annotated[AuthContext, Depends(require(Permission.READ))]


def investigation_out(inv: Investigation, steps: list[AgentRun] | None = None) -> InvestigationOut:
    out = InvestigationOut.model_validate(inv, from_attributes=True)
    if steps is not None:
        out.steps = [AgentStepOut.model_validate(s, from_attributes=True) for s in steps]
    return out


@router.post(
    "/anomalies/{anomaly_id}/investigate",
    response_model=InvestigationOut,
    status_code=202,
    summary="Start an evidence-backed investigation (deterministic unless an LLM is configured)",
)
def investigate_anomaly(
    anomaly_id: uuid.UUID,
    request: Request,
    key: IdempotencyKey,
    auth: Annotated[AuthContext, Depends(require(Permission.INVESTIGATIONS_RUN))],
    session: DbSession,
    settings: AppSettings,
    body: InvestigateRequest | None = None,
) -> InvestigationOut | JSONResponse:
    body = body or InvestigateRequest()
    guard = IdempotencyGuard(session, auth, key, "POST", request.url.path, body.model_dump())
    if (replayed := guard.replay()) is not None:
        return replayed
    anomaly = get_owned(session, Anomaly, auth, anomaly_id, label="anomaly")
    inv = service.request_investigation(
        session,
        settings,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        anomaly_id=anomaly.id,
        question=body.question,
    )
    record_audit(
        session,
        event_type="investigation.requested",
        tenant_id=auth.tenant_id,
        actor_id=auth.user_id,
        actor_label=auth.email,
        target_type="investigation",
        target_id=inv.id,
        payload={"anomaly_id": str(anomaly.id), "mode": inv.mode},
    )
    out = investigation_out(inv)
    guard.remember(202, out)
    session.commit()
    return out


@router.get("/investigations", response_model=InvestigationList, summary="List investigations")
def list_investigations(
    auth: Reader,
    session: DbSession,
    anomaly_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> InvestigationList:
    stmt = select(Investigation).where(Investigation.tenant_id == auth.tenant_id)
    if anomaly_id:
        stmt = stmt.where(Investigation.anomaly_id == anomaly_id)
    rows, next_cursor = paginate(
        session, stmt, Investigation.created_at, Investigation.id, limit, cursor
    )
    return InvestigationList(items=[investigation_out(i) for i in rows], next_cursor=next_cursor)


@router.get(
    "/investigations/{investigation_id}",
    response_model=InvestigationOut,
    summary="Investigation with steps",
)
def get_investigation(
    investigation_id: uuid.UUID, auth: Reader, session: DbSession
) -> InvestigationOut:
    inv = get_owned(session, Investigation, auth, investigation_id, label="investigation")
    steps = session.scalars(
        select(AgentRun)
        .where(AgentRun.investigation_id == inv.id, AgentRun.tenant_id == auth.tenant_id)
        .order_by(AgentRun.step_index)
    ).all()
    return investigation_out(inv, list(steps))


@router.get(
    "/investigations/{investigation_id}/report.md",
    response_class=PlainTextResponse,
    summary="Replayable Markdown report",
)
def investigation_markdown(
    investigation_id: uuid.UUID, auth: Reader, session: DbSession
) -> PlainTextResponse:
    inv = get_owned(session, Investigation, auth, investigation_id, label="investigation")
    return PlainTextResponse(
        service.render_markdown(inv),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="investigation-{inv.id}.md"'},
    )


@router.post(
    "/investigations/{investigation_id}/cancel",
    response_model=InvestigationOut,
    summary="Request cancellation",
)
def cancel_investigation(
    investigation_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(require(Permission.INVESTIGATIONS_RUN))],
    session: DbSession,
) -> InvestigationOut:
    inv = get_owned(
        session, Investigation, auth, investigation_id, label="investigation", for_update=True
    )
    if inv.state not in ("queued", "running"):
        raise ApiError(409, "invalid_state", f"Investigation is already {inv.state}")
    inv.cancel_requested = True
    record_audit(
        session,
        event_type="investigation.cancel_requested",
        tenant_id=auth.tenant_id,
        actor_id=auth.user_id,
        actor_label=auth.email,
        target_type="investigation",
        target_id=inv.id,
    )
    session.commit()
    return investigation_out(inv)
