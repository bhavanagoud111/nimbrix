"""Creating import requests (shared by the API and the demo seeder)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from nimbrix_api.models import BillingImport
from nimbrix_api.modules.ingestion import service, synthetic
from nimbrix_api.modules.jobs import queue


@dataclass(frozen=True)
class DemoImportRequested:
    billing_import: BillingImport
    duplicate: bool


def request_demo_import(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    end_date: date,
    days: int,
    seed: int,
) -> DemoImportRequested:
    checksum = service.checksum_of(synthetic.GENERATOR_VERSION, str(end_date), str(days), str(seed))
    original = service.find_completed_duplicate(session, tenant_id, "demo", checksum)
    if original is not None:
        duplicate = BillingImport(
            tenant_id=tenant_id,
            source="demo",
            state="duplicate",
            checksum=checksum,
            duplicate_of=original.id,
            requested_by=user_id,
            period_start=original.period_start,
            period_end=original.period_end,
            errors=[],
            details={"message": "identical data was already imported; nothing to do"},
            finished_at=datetime.now(UTC),
        )
        session.add(duplicate)
        session.flush()
        return DemoImportRequested(duplicate, duplicate=True)

    row = BillingImport(
        tenant_id=tenant_id,
        source="demo",
        state="queued",
        checksum=checksum,
        requested_by=user_id,
        errors=[],
        details={"end_date": str(end_date), "days": days, "seed": seed},
    )
    session.add(row)
    session.flush()
    queue.enqueue(
        session,
        tenant_id=tenant_id,
        kind="import_demo",
        payload={"import_id": str(row.id), "end_date": str(end_date), "days": days, "seed": seed},
        created_by=user_id,
        dedupe_key=f"import:{checksum}",
    )
    return DemoImportRequested(row, duplicate=False)
