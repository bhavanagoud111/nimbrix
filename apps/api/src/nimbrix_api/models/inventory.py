"""Observed cloud resources and their utilization evidence."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nimbrix_api.db.base import Base, TenantOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ResourceInventory(UUIDPrimaryKeyMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "resource_inventory"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "resource_id"),
        Index("ix_resource_inventory_tenant_id_resource_type", "tenant_id", "resource_type"),
    )

    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    region: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(512), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(256))
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    tags: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    environment: Mapped[str | None] = mapped_column(String(64))
    team: Mapped[str | None] = mapped_column(String(128))
    attributes: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    # On-demand list price per hour (or month for storage) used for savings math, if known.
    hourly_cost: Mapped[Decimal | None] = mapped_column()
    monthly_cost: Mapped[Decimal | None] = mapped_column()
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    utilization: Mapped[dict[str, Any] | None] = mapped_column()
    observed_at: Mapped[datetime] = mapped_column(nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("billing_imports.id", ondelete="SET NULL")
    )
