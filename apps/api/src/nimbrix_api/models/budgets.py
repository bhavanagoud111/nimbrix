"""Budgets and threshold alerts."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nimbrix_api.db.base import Base, TenantOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin, one_of

BUDGET_SCOPES = ("total", "account", "service", "team", "environment")


class Budget(UUIDPrimaryKeyMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name"),
        one_of("scope_type", BUDGET_SCOPES),
        one_of("period", ("monthly",)),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_value: Mapped[str | None] = mapped_column(String(256))
    period: Mapped[str] = mapped_column(String(16), nullable=False, default="monthly")
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    # Utilization ratios that raise alerts, e.g. [0.8, 1.0].
    alert_thresholds: Mapped[list[Any]] = mapped_column(nullable=False, default=lambda: [0.8, 1.0])
    owner: Mapped[str | None] = mapped_column(String(128))


class BudgetAlert(UUIDPrimaryKeyMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "budget_alerts"
    __table_args__ = (
        UniqueConstraint("budget_id", "period_start", "threshold", "kind"),
        one_of("kind", ("actual", "forecast")),
        one_of("status", ("open", "acknowledged", "resolved")),
    )

    budget_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budgets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    threshold: Mapped[Decimal] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    utilization: Mapped[Decimal] = mapped_column(nullable=False)
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    details: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
