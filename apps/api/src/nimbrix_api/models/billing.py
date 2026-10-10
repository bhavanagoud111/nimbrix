"""Billing imports and normalized cost line items."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nimbrix_api.db.base import (
    Base,
    Quantity,
    TenantOwnedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    one_of,
)

IMPORT_SOURCES = ("demo", "csv", "aws_cost_explorer", "aws_cur")
IMPORT_STATES = ("queued", "running", "succeeded", "partially_failed", "failed", "duplicate")


class BillingImport(UUIDPrimaryKeyMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "billing_imports"
    __table_args__ = (
        one_of("source", IMPORT_SOURCES),
        one_of("state", IMPORT_STATES),
        Index("ix_billing_imports_tenant_id_checksum", "tenant_id", "source", "checksum"),
        Index("ix_billing_imports_tenant_id_created_at", "tenant_id", "created_at"),
    )

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    checksum: Mapped[str | None] = mapped_column(String(64))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    filename: Mapped[str | None] = mapped_column(String(255))
    rows_received: Mapped[int] = mapped_column(nullable=False, default=0)
    rows_inserted: Mapped[int] = mapped_column(nullable=False, default=0)
    rows_updated: Mapped[int] = mapped_column(nullable=False, default=0)
    rows_unchanged: Mapped[int] = mapped_column(nullable=False, default=0)
    rows_rejected: Mapped[int] = mapped_column(nullable=False, default=0)
    resources_upserted: Mapped[int] = mapped_column(nullable=False, default=0)
    errors: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    details: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("billing_imports.id"))
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cloud_connections.id", ondelete="SET NULL")
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()


class CostLineItem(UUIDPrimaryKeyMixin, TenantOwnedMixin, TimestampMixin, Base):
    """One normalized billing record. Amounts are exact decimals in an explicit currency."""

    __tablename__ = "cost_line_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "source_record_id"),
        CheckConstraint("usage_end >= usage_start", name="usage_window"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso"),
        Index("ix_cost_line_items_tenant_id_usage_date", "tenant_id", "usage_date"),
        Index(
            "ix_cost_line_items_tenant_id_service_usage_date", "tenant_id", "service", "usage_date"
        ),
        Index("ix_cost_line_items_tenant_id_resource_id", "tenant_id", "resource_id"),
    )

    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(256), nullable=False)
    import_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("billing_imports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    service: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[str | None] = mapped_column(String(512))
    usage_type: Mapped[str | None] = mapped_column(String(256))
    usage_start: Mapped[datetime] = mapped_column(nullable=False)
    usage_end: Mapped[datetime] = mapped_column(nullable=False)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    usage_quantity: Mapped[Decimal | None] = mapped_column(Quantity)
    usage_unit: Mapped[str | None] = mapped_column(String(64))
    tags: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    # Allocation dimensions derived from tags at ingestion; NULL means unallocated.
    environment: Mapped[str | None] = mapped_column(String(64))
    team: Mapped[str | None] = mapped_column(String(128))
    provenance: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
