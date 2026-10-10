"""Cost anomalies and their lifecycle."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from nimbrix_api.api.v1.paths import API_V1_PREFIX
from nimbrix_api.core.audit import record_audit
from nimbrix_api.core.deps import AuthContext, DbSession, require
from nimbrix_api.core.errors import ApiError
from nimbrix_api.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, paginate
from nimbrix_api.core.permissions import Permission
from nimbrix_api.core.tenancy import get_owned
from nimbrix_api.models import Anomaly
from nimbrix_api.modules.ml.schemas import AnomalyList, AnomalyOut, AnomalyStatusUpdate

router = APIRouter(prefix=API_V1_PREFIX, tags=["anomalies"])
Reader = Annotated[AuthContext, Depends(require(Permission.READ))]

ALLOWED_TRANSITIONS = {
    "open": {"acknowledged", "resolved", "dismissed"},
    "acknowledged": {"resolved", "dismissed", "open"},
    "resolved": {"open"},
    "dismissed": {"open"},
}


def anomaly_out(a: Anomaly) -> AnomalyOut:
    return AnomalyOut.model_validate(a, from_attributes=True)


@router.get("/anomalies", response_model=AnomalyList, summary="Detected cost anomalies")
def list_anomalies(
    auth: Reader,
    session: DbSession,
    status: Annotated[str | None, Query(max_length=16)] = None,
    severity: Annotated[str | None, Query(max_length=16)] = None,
    scope_type: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> AnomalyList:
    stmt = select(Anomaly).where(Anomaly.tenant_id == auth.tenant_id)
    for column, value in (
        (Anomaly.status, status),
        (Anomaly.severity, severity),
        (Anomaly.scope_type, scope_type),
    ):
        if value:
            stmt = stmt.where(column == value)
    rows, next_cursor = paginate(session, stmt, Anomaly.created_at, Anomaly.id, limit, cursor)
    return AnomalyList(items=[anomaly_out(a) for a in rows], next_cursor=next_cursor)


@router.get("/anomalies/{anomaly_id}", response_model=AnomalyOut, summary="Anomaly with evidence")
def get_anomaly(anomaly_id: uuid.UUID, auth: Reader, session: DbSession) -> AnomalyOut:
    return anomaly_out(get_owned(session, Anomaly, auth, anomaly_id, label="anomaly"))


@router.post(
    "/anomalies/{anomaly_id}/status", response_model=AnomalyOut, summary="Change anomaly status"
)
def update_anomaly_status(
    anomaly_id: uuid.UUID,
    body: AnomalyStatusUpdate,
    auth: Annotated[AuthContext, Depends(require(Permission.ANOMALIES_UPDATE))],
    session: DbSession,
) -> AnomalyOut:
    anomaly = get_owned(session, Anomaly, auth, anomaly_id, label="anomaly", for_update=True)
    if body.status not in ALLOWED_TRANSITIONS[anomaly.status]:
        raise ApiError(
            409,
            "invalid_transition",
            f"Cannot move an anomaly from '{anomaly.status}' to '{body.status}'",
        )
    previous = anomaly.status
    anomaly.status = body.status
    anomaly.status_changed_by = auth.user_id
    record_audit(
        session,
        event_type="anomaly.status_changed",
        tenant_id=auth.tenant_id,
        actor_id=auth.user_id,
        actor_label=auth.email,
        target_type="anomaly",
        target_id=anomaly.id,
        payload={"from": previous, "to": body.status, "note": body.note},
    )
    session.commit()
    return anomaly_out(anomaly)
