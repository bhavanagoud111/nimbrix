"""Cloud connection metadata (no credentials are ever accepted or stored)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from nimbrix_api.api.v1.jobs import job_out
from nimbrix_api.api.v1.paths import API_V1_PREFIX
from nimbrix_api.core.audit import record_audit
from nimbrix_api.core.config import Settings
from nimbrix_api.core.deps import AppSettings, AuthContext, DbSession, IdempotencyKey, require
from nimbrix_api.core.errors import ApiError
from nimbrix_api.core.idempotency import IdempotencyGuard
from nimbrix_api.core.permissions import Permission
from nimbrix_api.core.tenancy import get_owned
from nimbrix_api.models import BillingImport, CloudConnection
from nimbrix_api.modules.connections.schemas import ConnectionCreate, ConnectionList, ConnectionOut
from nimbrix_api.modules.ingestion import aws
from nimbrix_api.modules.ingestion.schemas import (
    AwsSyncRequest,
    AwsSyncResponse,
    AwsValidateRequest,
    AwsValidateResponse,
    ValidationCheckOut,
)
from nimbrix_api.modules.jobs import queue
from nimbrix_api.modules.jobs.queue import PermanentJobError

router = APIRouter(prefix=API_V1_PREFIX, tags=["connections"])


def aws_factory(settings: Settings) -> aws.AwsSessionFactory:
    """Overridable in tests to inject a botocore-stubbed base session."""
    return aws.AwsSessionFactory(settings)


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


@router.post(
    "/connections/aws/validate",
    response_model=AwsValidateResponse,
    summary="Validate a read-only AWS role (no keys are accepted or stored)",
)
def validate_aws_connection(
    body: AwsValidateRequest,
    auth: Annotated[AuthContext, Depends(require(Permission.CONNECTIONS_WRITE))],
    session: DbSession,
    settings: AppSettings,
) -> AwsValidateResponse:
    connection = get_owned(
        session, CloudConnection, auth, body.connection_id, label="cloud_connection"
    )
    if not settings.aws_enabled:
        raise ApiError(
            409,
            "aws_disabled",
            "AWS access is disabled (NIMBRIX_AWS_ENABLED=false). The offline demo needs no AWS.",
        )
    try:
        boto_session = aws_factory(settings).for_connection(connection)
        checks = aws.validate_connection(
            boto_session, connection, check_cost_explorer=body.check_cost_explorer
        )
    except PermanentJobError as exc:
        checks = [aws.ValidationCheck("assume_role", False, str(exc))]
    passed = all(c.passed for c in checks)
    connection.status = "validated" if passed else "failed"
    connection.last_validated_at = datetime.now(UTC)
    connection.last_error = (
        None if passed else "; ".join(c.detail for c in checks if not c.passed)[:1000]
    )
    record_audit(
        session,
        event_type="connection.validated",
        tenant_id=auth.tenant_id,
        actor_id=auth.user_id,
        actor_label=auth.email,
        target_type="cloud_connection",
        target_id=connection.id,
        outcome="success" if passed else "failure",
        payload={"checks": [c.name for c in checks if c.passed]},
    )
    session.commit()
    return AwsValidateResponse(
        connection_id=connection.id,
        status=connection.status,
        checks=[ValidationCheckOut(name=c.name, passed=c.passed, detail=c.detail) for c in checks],
    )


@router.post(
    "/connections/{connection_id}/sync",
    response_model=AwsSyncResponse,
    status_code=202,
    summary="Queue a read-only Cost Explorer import and inventory sync",
)
def sync_aws_connection(
    connection_id: uuid.UUID,
    body: AwsSyncRequest,
    request: Request,
    key: IdempotencyKey,
    auth: Annotated[AuthContext, Depends(require(Permission.CONNECTIONS_WRITE))],
    session: DbSession,
    settings: AppSettings,
) -> AwsSyncResponse | JSONResponse:
    guard = IdempotencyGuard(
        session, auth, key, "POST", request.url.path, body.model_dump(mode="json")
    )
    if (replayed := guard.replay()) is not None:
        return replayed
    connection = get_owned(session, CloudConnection, auth, connection_id, label="cloud_connection")
    if not settings.aws_enabled:
        raise ApiError(409, "aws_disabled", "AWS access is disabled (NIMBRIX_AWS_ENABLED=false)")
    if connection.status != "validated":
        raise ApiError(409, "connection_not_validated", "Validate the connection before syncing")
    if body.end <= body.start or (body.end - body.start).days > 92:
        raise ApiError(
            422, "invalid_period", "end must be after start and the window at most 92 days"
        )
    row = BillingImport(
        tenant_id=auth.tenant_id,
        source="aws_cost_explorer",
        state="queued",
        connection_id=connection.id,
        requested_by=auth.user_id,
        period_start=body.start,
        period_end=body.end,
        errors=[],
        details={"granularity": "DAILY"},
    )
    session.add(row)
    session.flush()
    jobs = [
        queue.enqueue(
            session,
            tenant_id=auth.tenant_id,
            kind="import_aws_cost_explorer",
            payload={
                "import_id": str(row.id),
                "connection_id": str(connection.id),
                "start": str(body.start),
                "end": str(body.end),
            },
            created_by=auth.user_id,
            dedupe_key=f"ce:{connection.id}:{body.start}:{body.end}",
        )
    ]
    if body.include_inventory:
        jobs.append(
            queue.enqueue(
                session,
                tenant_id=auth.tenant_id,
                kind="sync_aws_inventory",
                payload={"connection_id": str(connection.id)},
                created_by=auth.user_id,
                dedupe_key=f"inventory:{connection.id}",
            )
        )
    record_audit(
        session,
        event_type="connection.sync_requested",
        tenant_id=auth.tenant_id,
        actor_id=auth.user_id,
        actor_label=auth.email,
        target_type="cloud_connection",
        target_id=connection.id,
        payload={"start": str(body.start), "end": str(body.end)},
    )
    out = AwsSyncResponse(import_id=row.id, jobs=[job_out(j) for j in jobs])
    guard.remember(202, out)
    session.commit()
    return out
