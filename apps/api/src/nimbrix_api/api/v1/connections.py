"""Cloud connection metadata (no credentials are ever accepted or stored)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from nimbrix_api.api.v1.paths import API_V1_PREFIX
from nimbrix_api.core.audit import record_audit
from nimbrix_api.core.deps import AuthContext, DbSession, IdempotencyKey, require
from nimbrix_api.core.errors import ApiError
from nimbrix_api.core.idempotency import IdempotencyGuard
from nimbrix_api.core.permissions import Permission
from nimbrix_api.core.tenancy import get_owned
from nimbrix_api.models import CloudConnection
from nimbrix_api.modules.connections.schemas import ConnectionCreate, ConnectionList, ConnectionOut

router = APIRouter(prefix=API_V1_PREFIX, tags=["connections"])


def to_out(c: CloudConnection) -> ConnectionOut:
    return ConnectionOut(
        id=c.id,
        provider=c.provider,
        name=c.name,
        account_id=c.account_id,
        role_arn=c.role_arn,
        has_external_id=bool(c.external_id),
        regions=list(c.regions),
        access_mode=c.access_mode,
        status=c.status,
        last_validated_at=c.last_validated_at,
        last_error=c.last_error,
        created_at=c.created_at,
    )


@router.get("/connections", response_model=ConnectionList, summary="List cloud connections")
def list_connections(
    auth: Annotated[AuthContext, Depends(require(Permission.CONNECTIONS_READ))], session: DbSession
) -> ConnectionList:
    rows = session.scalars(
        select(CloudConnection)
        .where(CloudConnection.tenant_id == auth.tenant_id)
        .order_by(CloudConnection.created_at)
    ).all()
    return ConnectionList(items=[to_out(c) for c in rows])


@router.get(
    "/connections/{connection_id}", response_model=ConnectionOut, summary="Get a connection"
)
def get_connection(
    connection_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(require(Permission.CONNECTIONS_READ))],
    session: DbSession,
) -> ConnectionOut:
    return to_out(
        get_owned(session, CloudConnection, auth, connection_id, label="cloud_connection")
    )


@router.post(
    "/connections",
    response_model=ConnectionOut,
    status_code=201,
    summary="Register connection metadata (read-only role reference, no keys)",
)
def create_connection(
    body: ConnectionCreate,
    request: Request,
    key: IdempotencyKey,
    auth: Annotated[AuthContext, Depends(require(Permission.CONNECTIONS_WRITE))],
    session: DbSession,
) -> ConnectionOut | JSONResponse:
    guard = IdempotencyGuard(session, auth, key, "POST", request.url.path, body.model_dump())
    if (replayed := guard.replay()) is not None:
        return replayed
    if body.role_arn:
        arn_account = body.role_arn.split(":")[4]
        if arn_account != body.account_id:
            raise ApiError(422, "role_account_mismatch", "role_arn must belong to account_id")
    connection = CloudConnection(
        tenant_id=auth.tenant_id,
        provider=body.provider,
        name=body.name,
        account_id=body.account_id,
        role_arn=body.role_arn,
        external_id=body.external_id,
        regions=body.regions,
        access_mode="read_only",
        status="pending",
        settings={},
        created_by=auth.user_id,
    )
    session.add(connection)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ApiError(409, "connection_exists", "A connection for this account exists") from None
    record_audit(
        session,
        event_type="connection.created",
        tenant_id=auth.tenant_id,
        actor_id=auth.user_id,
        actor_label=auth.email,
        target_type="cloud_connection",
        target_id=connection.id,
        payload={"account_id": body.account_id, "role_arn": body.role_arn, "regions": body.regions},
    )
    session.refresh(connection)
    out = to_out(connection)
    guard.remember(201, out)
    session.commit()
    return out
