"""Read-only audit trail."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from nimbrix_api.api.v1.paths import API_V1_PREFIX
from nimbrix_api.core.deps import AuthContext, DbSession, require
from nimbrix_api.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, PageInfo, paginate
from nimbrix_api.core.permissions import Permission
from nimbrix_api.models import AuditEvent

router = APIRouter(prefix=API_V1_PREFIX, tags=["audit"])


class AuditEventOut(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    actor_type: str
    actor_id: uuid.UUID | None
    actor_label: str | None
    event_type: str
    target_type: str | None
    target_id: str | None
    outcome: str
    request_id: str | None
    payload: dict[str, Any]


class AuditEventPage(BaseModel):
    items: list[AuditEventOut]
    page: PageInfo


@router.get("/audit-events", response_model=AuditEventPage, summary="Append-only audit trail")
def list_audit_events(
    auth: Annotated[AuthContext, Depends(require(Permission.AUDIT_READ))],
    session: DbSession,
    event_type: Annotated[str | None, Query(max_length=64)] = None,
    target_type: Annotated[str | None, Query(max_length=64)] = None,
    target_id: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> AuditEventPage:
    stmt = select(AuditEvent).where(AuditEvent.tenant_id == auth.tenant_id)
    if event_type:
        stmt = stmt.where(AuditEvent.event_type.startswith(event_type, autoescape=True))
    if target_type:
        stmt = stmt.where(AuditEvent.target_type == target_type)
    if target_id:
        stmt = stmt.where(AuditEvent.target_id == target_id)
    rows, next_cursor = paginate(
        session, stmt, AuditEvent.occurred_at, AuditEvent.id, limit, cursor
    )
    return AuditEventPage(
        items=[AuditEventOut.model_validate(r, from_attributes=True) for r in rows],
        page=PageInfo(limit=limit, next_cursor=next_cursor),
    )
