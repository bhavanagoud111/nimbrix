"""Anomalies, forecasts, and recommendations produced by deterministic/ML analysis."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nimbrix_api.db.base import Base, TenantOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin, one_of

ANOMALY_STATUSES = ("open", "acknowledged", "resolved", "dismissed")
SEVERITIES = ("low", "medium", "high")
CONFIDENCE = ("low", "medium", "high", "insufficient_evidence")
RECOMMENDATION_STATUSES = (
    "open",
    "insufficient_evidence",
    "dismissed",
    "approval_requested",
    "actioned",
    "superseded",
)


class Anomaly(UUIDPrimaryKeyMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "anomalies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key"),
        one_of("status", ANOMALY_STATUSES),
        one_of("severity", SEVERITIES),
        Index("ix_anomalies_tenant_id_window_start", "tenant_id", "window_start"),
    )

    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False)
    detector_version: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_value: Mapped[str] = mapped_column(String(512), nullable=False)
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)
    expected: Mapped[Decimal] = mapped_column(nullable=False)
    observed: Mapped[Decimal] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    score: Mapped[float] = mapped_column(nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    evidence: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    first_detected_at: Mapped[datetime] = mapped_column(nullable=False)
    last_detected_at: Mapped[datetime] = mapped_column(nullable=False)
    status_changed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class Forecast(UUIDPrimaryKeyMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "forecasts"
    __table_args__ = (
        one_of("status", ("succeeded", "insufficient_data", "failed")),
        Index(
            "ix_forecasts_tenant_id_scope_created_at",
            "tenant_id",
            "scope_type",
            "scope_value",
            "created_at",
        ),
    )

    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_value: Mapped[str] = mapped_column(String(512), nullable=False)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(64), nullable=False)
    horizon_days: Mapped[int] = mapped_column(nullable=False)
    interval_level: Mapped[float] = mapped_column(nullable=False)
    training_start: Mapped[date | None] = mapped_column(Date)
    training_end: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    points: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    metrics: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    message: Mapped[str | None] = mapped_column(String(1000))


class Recommendation(UUIDPrimaryKeyMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key"),
        one_of("status", RECOMMENDATION_STATUSES),
        one_of("confidence", CONFIDENCE),
        one_of("risk", SEVERITIES),
        Index("ix_recommendations_tenant_id_status", "tenant_id", "status"),
    )

    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(String(2000), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(512), index=True)
    account_id: Mapped[str | None] = mapped_column(String(32))
    region: Mapped[str | None] = mapped_column(String(32))
    affected_resources: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    evidence: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    eligibility: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    assumptions: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    savings_formula: Mapped[str | None] = mapped_column(String(1000))
    estimated_monthly_savings: Mapped[Decimal | None] = mapped_column()
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    owner: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    utilization_window: Mapped[dict[str, Any] | None] = mapped_column()
    last_simulation: Mapped[dict[str, Any] | None] = mapped_column()
    proposed_action: Mapped[str | None] = mapped_column(String(64))
    last_evaluated_at: Mapped[datetime] = mapped_column(nullable=False)
