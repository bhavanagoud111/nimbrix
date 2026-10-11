"""Retention enforcement (docs/operations.md). Runs as a daily job per process, not per tenant.

* idempotency records: deleted after expiry (24 h);
* sessions: deleted once expired or revoked for ``session_retention_days``;
* raw uploaded CSV bytes: deleted after ``raw_upload_retention_days`` (the parsed line items and
  the import record, including its checksum and row errors, are kept);
* audit events are never deleted by the application (append-only trigger); see operations.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_
from sqlalchemy.orm import Session

from nimbrix_api.core.config import Settings
from nimbrix_api.models import IdempotencyRecord, ImportFile
from nimbrix_api.models import Session as SessionRow


def run_retention(
    session: Session, settings: Settings, now: datetime | None = None
) -> dict[str, int]:
    now = now or datetime.now(UTC)
    session_cutoff = now - timedelta(days=settings.session_retention_days)
    results = {
        "idempotency_keys": session.execute(
            delete(IdempotencyRecord)
            .where(IdempotencyRecord.expires_at < now)
            .returning(IdempotencyRecord.id)
        ).all(),
        "sessions": session.execute(
            delete(SessionRow)
            .where(
                or_(SessionRow.expires_at < session_cutoff, SessionRow.revoked_at < session_cutoff)
            )
            .returning(SessionRow.id)
        ).all(),
        "raw_uploads": session.execute(
            delete(ImportFile)
            .where(ImportFile.created_at < now - timedelta(days=settings.raw_upload_retention_days))
            .returning(ImportFile.id)
        ).all(),
    }
    session.commit()
    return {k: len(v) for k, v in results.items()}
