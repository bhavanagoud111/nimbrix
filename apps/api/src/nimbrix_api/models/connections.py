"""Cloud connection metadata. Never stores credentials — only a role reference."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nimbrix_api.db.base import Base, TenantOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin, one_of

CONNECTION_STATUSES = ("pending", "validated", "failed", "disabled")


class CloudConnection(UUIDPrimaryKeyMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "cloud_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "account_id"),
        one_of("provider", ("aws",)),
        one_of("status", CONNECTION_STATUSES),
        one_of("access_mode", ("read_only",)),
    )

    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    # IAM role Nimbrix assumes (read-only). The external ID is a non-secret confused-deputy guard.
    role_arn: Mapped[str | None] = mapped_column(String(2048))
    external_id: Mapped[str | None] = mapped_column(String(1224))
    regions: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    access_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="read_only")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    last_validated_at: Mapped[datetime | None] = mapped_column()
    last_error: Mapped[str | None] = mapped_column(String(1000))
    settings: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
