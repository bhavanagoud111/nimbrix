"""Investigations and the per-step agent run log."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nimbrix_api.db.base import (
    Base,
    CreatedAtMixin,
    TenantOwnedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    one_of,
)

INVESTIGATION_STATES = ("queued", "running", "completed", "failed", "cancelled")


class Investigation(UUIDPrimaryKeyMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "investigations"
    __table_args__ = (
        one_of("state", INVESTIGATION_STATES),
        Index("ix_investigations_tenant_id_created_at", "tenant_id", "created_at"),
    )

    anomaly_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("anomalies.id", ondelete="SET NULL"), index=True
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    # deterministic | openai | anthropic | ollama — which engine produced the report.
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str | None] = mapped_column(String(2000))
    report: Mapped[dict[str, Any] | None] = mapped_column()
    evidence: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    budget: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    usage: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(String(2000))
    cancel_requested: Mapped[bool] = mapped_column(nullable=False, default=False)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()


class AgentRun(UUIDPrimaryKeyMixin, TenantOwnedMixin, CreatedAtMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (UniqueConstraint("investigation_id", "step_index"),)

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_index: Mapped[int] = mapped_column(nullable=False)
    node: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_role: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    output: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    evidence_refs: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    tool_calls: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    tokens_in: Mapped[int | None] = mapped_column()
    tokens_out: Mapped[int | None] = mapped_column()
    cost_usd: Mapped[Decimal | None] = mapped_column()
    latency_ms: Mapped[float] = mapped_column(nullable=False, default=0.0)
    error: Mapped[str | None] = mapped_column(String(2000))
