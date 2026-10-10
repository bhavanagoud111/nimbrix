"""Action registry, approval requests, decisions, and simulated remediation."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from nimbrix_api.api.v1.paths import API_V1_PREFIX
from nimbrix_api.core.deps import AppSettings, AuthContext, DbSession, IdempotencyKey, require
from nimbrix_api.core.idempotency import IdempotencyGuard
from nimbrix_api.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, paginate
from nimbrix_api.core.permissions import Permission
from nimbrix_api.core.tenancy import get_owned
from nimbrix_api.models import ApprovalRequest, RemediationExecution, User
from nimbrix_api.modules.remediation import service
from nimbrix_api.modules.remediation.registry import REGISTRY, canonical_payload_hash
from nimbrix_api.modules.remediation.schemas import (
    ActionList,
    ActionOut,
    ApprovalCreate,
    ApprovalDecision,
    ApprovalList,
    ApprovalOut,
    PolicyPreview,
    RejectDecision,
    RemediationCreate,
    RemediationList,
    RemediationOut,
)

router = APIRouter(prefix=API_V1_PREFIX, tags=["approvals"])
Reader = Annotated[AuthContext, Depends(require(Permission.READ))]
Requester = Annotated[AuthContext, Depends(require(Permission.APPROVALS_REQUEST))]
Decider = Annotated[AuthContext, Depends(require(Permission.APPROVALS_DECIDE))]
Executor = Annotated[AuthContext, Depends(require(Permission.REMEDIATIONS_EXECUTE))]


def approval_out(session: DbSession, a: ApprovalRequest) -> ApprovalOut:
    emails = dict(
        session.execute(
            select(User.id, User.email).where(User.id.in_([a.requested_by, a.decided_by]))
        ).all()
    )
    execution_id = session.scalar(
        select(RemediationExecution.id).where(RemediationExecution.approval_request_id == a.id)
    )
    out = ApprovalOut.model_validate(a, from_attributes=True)
    out.requested_by_email = emails.get(a.requested_by)
    out.decided_by_email = emails.get(a.decided_by) if a.decided_by else None
    out.execution_id = execution_id
    return out


@router.get("/actions", response_model=ActionList, summary="Allowlisted remediation actions")
def list_actions(auth: Reader, settings: AppSettings) -> ActionList:
    return ActionList(
        items=[
            ActionOut(
                name=s.name,
                description=s.description,
                mode=s.mode,
                payload_schema=s.payload_model.model_json_schema(),
            )
            for s in REGISTRY.values()
        ],
        live_actions_enabled=settings.live_actions_enabled,
    )


@router.post(
    "/approvals/preview",
    response_model=PolicyPreview,
    summary="Policy check and impact preview (creates nothing)",
)
def preview_approval(
    body: ApprovalCreate, auth: Requester, session: DbSession, settings: AppSettings
) -> PolicyPreview:
    result = service.preview(session, settings, auth, body.action_type, body.payload)
    return PolicyPreview(
        action_type=result.action_type,
        allowed=result.allowed,
        checks=result.checks,
        denial_reasons=result.denial_reasons,
        payload=result.payload,
        payload_hash=canonical_payload_hash(auth.tenant_id, body.action_type, result.payload)
        if result.payload
        else None,
        resource=result.resource,
        impact_preview=result.impact_preview,
        rollback_plan=result.rollback_plan,
    )


@router.post(
    "/approvals",
    response_model=ApprovalOut,
    status_code=201,
    summary="Request approval for an action",
)
def create_approval(
    body: ApprovalCreate,
    request: Request,
    key: IdempotencyKey,
    auth: Requester,
    session: DbSession,
    settings: AppSettings,
) -> ApprovalOut | JSONResponse:
    guard = IdempotencyGuard(
        session, auth, key, "POST", request.url.path, body.model_dump(mode="json")
    )
    if (replayed := guard.replay()) is not None:
        return replayed
    approval = service.create_request(
        session,
        settings,
        auth,
        action_type=body.action_type,
        payload=body.payload,
        recommendation_id=body.recommendation_id,
        idempotency_key=key,
    )
    out = approval_out(session, approval)
    guard.remember(201, out)
    session.commit()
    return out


@router.get("/approvals", response_model=ApprovalList, summary="Approval requests")
def list_approvals(
    auth: Reader,
    session: DbSession,
    status: Annotated[str | None, Query(max_length=16)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> ApprovalList:
    if service.expire_stale(session, auth.tenant_id):
        session.commit()
    stmt = select(ApprovalRequest).where(ApprovalRequest.tenant_id == auth.tenant_id)
    if status:
        stmt = stmt.where(ApprovalRequest.status == status)
    rows, next_cursor = paginate(
        session, stmt, ApprovalRequest.created_at, ApprovalRequest.id, limit, cursor
    )
    return ApprovalList(items=[approval_out(session, a) for a in rows], next_cursor=next_cursor)


@router.get("/approvals/{approval_id}", response_model=ApprovalOut, summary="One approval request")
def get_approval(approval_id: uuid.UUID, auth: Reader, session: DbSession) -> ApprovalOut:
    if service.expire_stale(session, auth.tenant_id):
        session.commit()
    return approval_out(
        session, get_owned(session, ApprovalRequest, auth, approval_id, label="approval_request")
    )


@router.post(
    "/approvals/{approval_id}/approve",
    response_model=ApprovalOut,
    summary="Approve (requires the reviewed payload_hash)",
)
def approve(
    approval_id: uuid.UUID,
    body: ApprovalDecision,
    auth: Decider,
    session: DbSession,
    settings: AppSettings,
) -> ApprovalOut:
    approval = service.decide(
        session,
        settings,
        auth,
        approval_id,
        approve=True,
        payload_hash=body.payload_hash,
        reason=body.reason,
    )
    session.commit()
    return approval_out(session, approval)


@router.post(
    "/approvals/{approval_id}/reject", response_model=ApprovalOut, summary="Reject with a reason"
)
def reject(
    approval_id: uuid.UUID,
    body: RejectDecision,
    auth: Decider,
    session: DbSession,
    settings: AppSettings,
) -> ApprovalOut:
    approval = service.decide(
        session, settings, auth, approval_id, approve=False, payload_hash=None, reason=body.reason
    )
    session.commit()
    return approval_out(session, approval)


@router.post(
    "/remediations",
    response_model=RemediationOut,
    status_code=202,
    summary="Execute an approved action (simulation)",
)
def create_remediation(
    body: RemediationCreate,
    request: Request,
    key: IdempotencyKey,
    auth: Executor,
    session: DbSession,
    settings: AppSettings,
) -> RemediationOut | JSONResponse:
    guard = IdempotencyGuard(
        session, auth, key, "POST", request.url.path, body.model_dump(mode="json")
    )
    if (replayed := guard.replay()) is not None:
        return replayed
    execution = service.request_execution(session, settings, auth, body.approval_request_id, key)
    out = RemediationOut.model_validate(execution, from_attributes=True)
    guard.remember(202, out)
    session.commit()
    return out


@router.get("/remediations", response_model=RemediationList, summary="Remediation executions")
def list_remediations(
    auth: Reader,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> RemediationList:
    stmt = select(RemediationExecution).where(RemediationExecution.tenant_id == auth.tenant_id)
    rows, next_cursor = paginate(
        session, stmt, RemediationExecution.created_at, RemediationExecution.id, limit, cursor
    )
    return RemediationList(
        items=[RemediationOut.model_validate(r, from_attributes=True) for r in rows],
        next_cursor=next_cursor,
    )


@router.get(
    "/remediations/{execution_id}",
    response_model=RemediationOut,
    summary="Execution outcome and verification",
)
def get_remediation(execution_id: uuid.UUID, auth: Reader, session: DbSession) -> RemediationOut:
    execution = get_owned(
        session, RemediationExecution, auth, execution_id, label="remediation_execution"
    )
    return RemediationOut.model_validate(execution, from_attributes=True)
