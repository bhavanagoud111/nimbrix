"""Approval requests, remediation executions, audit events, and idempotency records."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from nimbrix_api.db.base import (
    Base,
    TenantOwnedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    one_of,
)

APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired", "cancelled", "executed")
EXECUTION_STATES = ("pending", "running", "succeeded", "failed")


class ApprovalRequest(UUIDPrimaryKeyMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key"),
        one_of("status", APPROVAL_STATUSES),
        Index("ix_approval_requests_tenant_id_status", "tenant_id", "status"),
    )

    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    # SHA-256 over the canonical (tenant, action, payload) — binds the approval to exact content.
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target_resource_id: Mapped[str] = mapped_column(String(512), nullable=False)
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recommendations.id", ondelete="SET NULL")
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decision_reason: Mapped[str | None] = mapped_column(String(2000))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    policy_result: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    impact_preview: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    rollback_plan: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column()
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class RemediationExecution(UUIDPrimaryKeyMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "remediation_executions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key"),
        # Replay prevention: an approval can be executed at most once.
        UniqueConstraint("approval_request_id"),
        one_of("mode", ("simulation", "live")),
        one_of("state", EXECUTION_STATES),
    )

    approval_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("approval_requests.id"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    dry_run: Mapped[bool] = mapped_column(nullable=False, default=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    result: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    verification: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    rollback_plan: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(String(2000))
    executed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    """Append-only (enforced by a database trigger). Payloads are redacted before insert."""

    __tablename__ = "audit_events"
    __table_args__ = (
        one_of("actor_type", ("user", "system", "agent", "anonymous")),
        Index("ix_audit_events_tenant_id_occurred_at", "tenant_id", "occurred_at"),
        Index("ix_audit_events_target", "tenant_id", "target_type", "target_id"),
    )

    # Nullable: some security events (failed login for an unknown user) have no tenant.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"))
    occurred_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    actor_label: Mapped[str | None] = mapped_column(String(320))
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(512))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    request_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)


class IdempotencyRecord(UUIDPrimaryKeyMixin, TenantOwnedMixin, TimestampMixin, Base):
    """Stored response for a mutation so client retries with the same key are safe."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "key"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int | None] = mapped_column()
    response_body: Mapped[dict[str, Any] | None] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
