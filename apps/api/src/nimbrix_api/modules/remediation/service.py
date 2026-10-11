"""Approval and remediation state machine (ADR-0005).

request -> policy validation -> impact preview -> approval -> execution -> verification -> audit

Approval states: pending -> approved | rejected | expired | cancelled; approved -> executed.
Execution states: pending -> running -> succeeded | failed. Every transition is audited.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nimbrix_api.core.audit import record_audit
from nimbrix_api.core.business_metrics import APPROVAL_DECISIONS, POLICY_DENIALS, REMEDIATIONS
from nimbrix_api.core.config import Settings
from nimbrix_api.core.deps import AuthContext
from nimbrix_api.core.errors import ApiError, ErrorDetail
from nimbrix_api.core.tenancy import get_owned
from nimbrix_api.models import (
    ApprovalRequest,
    Recommendation,
    RemediationExecution,
    SimulatedResourceState,
)
from nimbrix_api.modules.jobs import queue
from nimbrix_api.modules.remediation.registry import (
    REGISTRY,
    PolicyResult,
    canonical_payload_hash,
    evaluate_policy,
)

policy_evaluator = evaluate_policy  # replaceable in tests (e.g. to drop the rollback plan)


def _audit(
    session: Session,
    auth: AuthContext | None,
    event: str,
    target: Any,
    *,
    outcome: str = "success",
    payload: dict[str, Any] | None = None,
    tenant_id: uuid.UUID | None = None,
    target_type: str = "approval_request",
) -> None:
    record_audit(
        session,
        event_type=event,
        tenant_id=auth.tenant_id if auth else tenant_id,
        actor_type="user" if auth else "system",
        actor_id=auth.user_id if auth else None,
        actor_label=auth.email if auth else "remediation-worker",
        target_type=target_type,
        target_id=target,
        outcome=outcome,
        payload=payload,
    )


def _policy_error(result: PolicyResult, code: str = "policy_denied", status: int = 422) -> ApiError:
    POLICY_DENIALS.labels(stage=code).inc()
    return ApiError(
        status,
        code,
        "The request violates remediation policy",
        [
            ErrorDetail(field=c["check"], message=c["detail"])
            for c in result.checks
            if not c["passed"]
        ],
    )


def preview(
    session: Session,
    settings: Settings,
    auth: AuthContext,
    action_type: str,
    payload: dict[str, Any],
) -> PolicyResult:
    return policy_evaluator(
        session,
        auth.tenant_id,
        action_type,
        payload,
        inventory_max_age_hours=settings.inventory_max_age_hours,
    )


def expire_stale(session: Session, tenant_id: uuid.UUID, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    expired = session.scalars(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.tenant_id == tenant_id,
            ApprovalRequest.status.in_(("pending", "approved")),
            ApprovalRequest.expires_at <= now,
        )
        .with_for_update(skip_locked=True)
    ).all()
    for approval in expired:
        approval.status = "expired"
        _audit(
            session, None, "approval.expired", approval.id, tenant_id=tenant_id, outcome="failure"
        )
    session.flush()
    return len(expired)


def create_request(
    session: Session,
    settings: Settings,
    auth: AuthContext,
    *,
    action_type: str,
    payload: dict[str, Any],
    recommendation_id: uuid.UUID | None,
    idempotency_key: str,
) -> ApprovalRequest:
    result = preview(session, settings, auth, action_type, payload)
    if not result.allowed or result.payload is None:
        _audit(
            session,
            auth,
            "approval.denied_by_policy",
            None,
            outcome="denied",
            payload={
                "action_type": action_type,
                "reasons": result.denial_reasons,
                "payload": payload,
            },
        )
        session.commit()
        raise _policy_error(result)
    if not result.rollback_plan or not result.rollback_plan.get("steps"):
        _audit(
            session,
            auth,
            "approval.denied_by_policy",
            None,
            outcome="denied",
            payload={"action_type": action_type, "reasons": ["missing rollback plan"]},
        )
        session.commit()
        raise ApiError(
            422, "missing_rollback_plan", "Every action requires a rollback plan before approval"
        )
    rec = None
    if recommendation_id is not None:
        rec = session.scalar(
            select(Recommendation)
            .where(
                Recommendation.id == recommendation_id, Recommendation.tenant_id == auth.tenant_id
            )
            .with_for_update()
        )
        if rec is None:
            raise ApiError(404, "not_found", "Recommendation not found")
        if rec.resource_id != result.payload["resource_id"]:
            raise ApiError(
                422, "recommendation_mismatch", "The recommendation targets a different resource"
            )
    approval = ApprovalRequest(
        tenant_id=auth.tenant_id,
        action_type=action_type,
        payload=result.payload,
        payload_hash=canonical_payload_hash(auth.tenant_id, action_type, result.payload),
        target_resource_id=result.payload["resource_id"],
        recommendation_id=recommendation_id,
        requested_by=auth.user_id,
        status="pending",
        policy_result=result.as_dict(),
        impact_preview=result.impact_preview,
        rollback_plan=result.rollback_plan,
        expires_at=datetime.now(UTC) + timedelta(hours=settings.approval_ttl_hours),
        idempotency_key=idempotency_key,
    )
    session.add(approval)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ApiError(
            409, "duplicate_request", "An approval request with this Idempotency-Key exists"
        ) from None
    if rec is not None:
        rec.status = "approval_requested"
    _audit(
        session,
        auth,
        "approval.requested",
        approval.id,
        payload={
            "action_type": action_type,
            "payload": result.payload,
            "payload_hash": approval.payload_hash,
        },
    )
    return approval


def _locked(session: Session, auth: AuthContext, approval_id: uuid.UUID) -> ApprovalRequest:
    return get_owned(
        session, ApprovalRequest, auth, approval_id, label="approval_request", for_update=True
    )


def _assert_integrity(approval: ApprovalRequest, presented_hash: str | None) -> None:
    recomputed = canonical_payload_hash(approval.tenant_id, approval.action_type, approval.payload)
    if recomputed != approval.payload_hash:
        raise ApiError(
            409, "payload_tampered", "Stored payload no longer matches its approval hash"
        )
    if presented_hash is not None and presented_hash != approval.payload_hash:
        raise ApiError(
            409, "payload_hash_mismatch", "The approved payload differs from the one reviewed"
        )


def _assert_not_expired(session: Session, approval: ApprovalRequest, auth: AuthContext) -> None:
    if approval.expires_at <= datetime.now(UTC):
        approval.status = "expired"
        _audit(session, auth, "approval.expired", approval.id, outcome="failure")
        session.commit()
        raise ApiError(409, "approval_expired", "This approval request has expired")


def decide(
    session: Session,
    settings: Settings,
    auth: AuthContext,
    approval_id: uuid.UUID,
    *,
    approve: bool,
    payload_hash: str | None,
    reason: str | None,
) -> ApprovalRequest:
    approval = _locked(session, auth, approval_id)
    if approval.status != "pending":
        raise ApiError(409, "invalid_state", f"Approval request is {approval.status}")
    _assert_not_expired(session, approval, auth)
    if approve:
        if settings.require_distinct_approver and approval.requested_by == auth.user_id:
            _audit(session, auth, "approval.self_approval_denied", approval.id, outcome="denied")
            session.commit()
            raise ApiError(
                403, "separation_of_duties", "Requesters cannot approve their own requests"
            )
        if payload_hash is None:
            raise ApiError(
                422,
                "payload_hash_required",
                "Approvals must include the payload_hash that was reviewed",
            )
        try:
            _assert_integrity(approval, payload_hash)
        except ApiError as exc:
            _audit(
                session,
                auth,
                "approval.integrity_failure",
                approval.id,
                outcome="denied",
                payload={"code": exc.code},
            )
            session.commit()
            raise
        result = preview(session, settings, auth, approval.action_type, approval.payload)
        if not result.allowed:
            _audit(
                session,
                auth,
                "approval.denied_by_policy",
                approval.id,
                outcome="denied",
                payload={"reasons": result.denial_reasons, "stage": "approval"},
            )
            session.commit()
            raise _policy_error(result, status=409)
        approval.status = "approved"
        approval.policy_result = result.as_dict()
    else:
        approval.status = "rejected"
        if approval.recommendation_id:
            session.execute(
                update(Recommendation)
                .where(
                    Recommendation.id == approval.recommendation_id,
                    Recommendation.status == "approval_requested",
                )
                .values(status="open")
            )
    APPROVAL_DECISIONS.labels(decision=approval.status).inc()
    approval.decided_by = auth.user_id
    approval.decided_at = datetime.now(UTC)
    approval.decision_reason = reason
    _audit(session, auth, f"approval.{approval.status}", approval.id, payload={"reason": reason})
    return approval


class SimulationAdapter(Protocol):
    def stop(
        self, session: Session, tenant_id: uuid.UUID, resource_id: str, execution_id: uuid.UUID
    ) -> dict[str, Any]: ...

    def verify(
        self, session: Session, tenant_id: uuid.UUID, resource_id: str
    ) -> dict[str, Any]: ...


class DatabaseSimulationAdapter:
    """Records a simulated state change. Makes no cloud call. Idempotent per resource."""

    def stop(
        self, session: Session, tenant_id: uuid.UUID, resource_id: str, execution_id: uuid.UUID
    ) -> dict[str, Any]:
        stmt = (
            insert(SimulatedResourceState)
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                resource_id=resource_id,
                simulated_state="stopped",
                execution_id=execution_id,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "resource_id"],
                set_={
                    "simulated_state": "stopped",
                    "execution_id": execution_id,
                    "updated_at": datetime.now(UTC),
                },
            )
        )
        session.execute(stmt)
        return {"simulated_state": "stopped", "cloud_api_called": False}

    def verify(self, session: Session, tenant_id: uuid.UUID, resource_id: str) -> dict[str, Any]:
        state = session.scalar(
            select(SimulatedResourceState.simulated_state).where(
                SimulatedResourceState.tenant_id == tenant_id,
                SimulatedResourceState.resource_id == resource_id,
            )
        )
        return {"verified": state == "stopped", "observed_simulated_state": state}


adapter: SimulationAdapter = DatabaseSimulationAdapter()


def request_execution(
    session: Session,
    settings: Settings,
    auth: AuthContext,
    approval_id: uuid.UUID,
    idempotency_key: str,
) -> RemediationExecution:
    approval = _locked(session, auth, approval_id)
    if approval.status == "executed" or session.scalar(
        select(RemediationExecution.id).where(
            RemediationExecution.approval_request_id == approval.id
        )
    ):
        raise ApiError(409, "already_executed", "This approval has already been executed")
    if approval.status != "approved":
        raise ApiError(409, "not_approved", f"Approval request is {approval.status}, not approved")
    _assert_not_expired(session, approval, auth)
    _assert_integrity(approval, None)
    spec = REGISTRY[approval.action_type]
    if spec.mode != "simulation" or settings.live_actions_enabled:
        raise ApiError(409, "live_actions_disabled", "Only simulated remediation is available")
    result = preview(session, settings, auth, approval.action_type, approval.payload)
    if not result.allowed:
        _audit(
            session,
            auth,
            "remediation.blocked",
            approval.id,
            outcome="denied",
            payload={"reasons": result.denial_reasons, "stage": "pre-execution revalidation"},
        )
        session.commit()
        raise _policy_error(result, code="revalidation_failed", status=409)
    execution = RemediationExecution(
        tenant_id=auth.tenant_id,
        approval_request_id=approval.id,
        idempotency_key=idempotency_key,
        action_type=approval.action_type,
        mode="simulation",
        dry_run=True,
        state="pending",
        rollback_plan=approval.rollback_plan,
        executed_by=auth.user_id,
        result={
            "before": {
                "state": result.resource["state"] if result.resource else None,
                "monthly_compute_cost": approval.impact_preview.get("current_monthly_compute_cost"),
            }
        },
    )
    session.add(execution)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ApiError(409, "already_executed", "This approval has already been executed") from None
    queue.enqueue(
        session,
        tenant_id=auth.tenant_id,
        kind="remediate",
        payload={"execution_id": str(execution.id)},
        created_by=auth.user_id,
        dedupe_key=f"remediate:{execution.id}",
        max_attempts=3,
    )
    _audit(
        session,
        auth,
        "remediation.requested",
        execution.id,
        target_type="remediation_execution",
        payload={"approval_request_id": str(approval.id), "mode": "simulation"},
    )
    return execution


def run_execution(
    session: Session, settings: Settings, tenant_id: uuid.UUID, execution_id: uuid.UUID
) -> dict[str, Any]:
    """Worker side. Safe to re-run after a crash: a finished execution is returned as-is."""
    execution = session.scalar(
        select(RemediationExecution)
        .where(RemediationExecution.id == execution_id, RemediationExecution.tenant_id == tenant_id)
        .with_for_update()
    )
    if execution is None:
        return {"state": "missing"}
    if execution.state in ("succeeded", "failed"):
        return {"state": execution.state, "replayed": True}
    approval = session.get(ApprovalRequest, execution.approval_request_id, with_for_update=True)
    assert approval is not None  # noqa: S101
    execution.state = "running"
    execution.attempts += 1
    execution.started_at = execution.started_at or datetime.now(UTC)
    now = datetime.now(UTC)
    revalidation = policy_evaluator(
        session,
        tenant_id,
        approval.action_type,
        approval.payload,
        inventory_max_age_hours=settings.inventory_max_age_hours,
        now=now,
    )
    integrity_ok = (
        canonical_payload_hash(tenant_id, approval.action_type, approval.payload)
        == approval.payload_hash
    )
    if not revalidation.allowed or not integrity_ok:
        execution.state = "failed"
        execution.error = "revalidation failed: " + "; ".join(
            revalidation.denial_reasons or ["payload integrity"]
        )
        execution.finished_at = now
        REMEDIATIONS.labels(
            action_type=approval.action_type, mode=execution.mode, state="failed"
        ).inc()
        _audit(
            session,
            None,
            "remediation.failed",
            execution.id,
            tenant_id=tenant_id,
            outcome="failure",
            target_type="remediation_execution",
            payload={"error": execution.error},
        )
        return {"state": "failed", "error": execution.error}
    resource_id = approval.payload["resource_id"]
    outcome = adapter.stop(session, tenant_id, resource_id, execution.id)
    verification = adapter.verify(session, tenant_id, resource_id)
    execution.verification = {**verification, "verified_at": datetime.now(UTC).isoformat()}
    if not verification["verified"]:
        execution.state = "failed"
        execution.error = "post-action verification failed"
    else:
        execution.state = "succeeded"
        approval.status = "executed"
        if approval.recommendation_id:
            session.execute(
                update(Recommendation)
                .where(Recommendation.id == approval.recommendation_id)
                .values(status="actioned")
            )
    execution.result = {
        **execution.result,
        "after": {
            "state": outcome["simulated_state"],
            "monthly_compute_cost": "0.00" if execution.state == "succeeded" else None,
        },
        "estimated_monthly_savings": approval.impact_preview.get("estimated_monthly_savings"),
        "mode": "simulation",
        "realized": False,
        "cloud_api_called": False,
    }
    execution.finished_at = datetime.now(UTC)
    REMEDIATIONS.labels(
        action_type=approval.action_type, mode=execution.mode, state=execution.state
    ).inc()
    _audit(
        session,
        None,
        f"remediation.{execution.state}",
        execution.id,
        tenant_id=tenant_id,
        outcome="success" if execution.state == "succeeded" else "failure",
        target_type="remediation_execution",
        payload={"resource_id": resource_id, "result": execution.result},
    )
    return {"state": execution.state}
