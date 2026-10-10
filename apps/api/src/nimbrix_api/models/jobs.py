"""Database-backed job queue (ADR-0003) and stored upload payloads."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, LargeBinary, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from nimbrix_api.db.base import Base, TenantOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin, one_of

JOB_STATES = ("queued", "running", "succeeded", "failed", "cancelled")


class Job(UUIDPrimaryKeyMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        one_of("state", JOB_STATES),
        Index("ix_jobs_claimable", "state", "run_after"),
        # At most one active job per dedupe key: scheduling the same work twice is a no-op.
        Index(
            "uq_jobs_active_dedupe_key",
            "tenant_id",
            "dedupe_key",
            unique=True,
            postgresql_where=text("state IN ('queued', 'running') AND dedupe_key IS NOT NULL"),
        ),
    )

    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(nullable=False, default=3)
    run_after: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    locked_by: Mapped[str | None] = mapped_column(String(128))
    locked_until: Mapped[datetime | None] = mapped_column()
    dedupe_key: Mapped[str | None] = mapped_column(String(256))
    result: Mapped[dict[str, Any] | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(String(2000))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()


class ImportFile(UUIDPrimaryKeyMixin, TenantOwnedMixin, TimestampMixin, Base):
    """Raw uploaded file, kept so the import job (and audits) can re-read exactly what was sent."""

    __tablename__ = "import_files"

    import_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("billing_imports.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
