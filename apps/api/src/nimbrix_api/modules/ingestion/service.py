"""Persist normalized records: idempotent upserts with provenance and accurate counts."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from nimbrix_api.core.audit import record_audit
from nimbrix_api.models import BillingImport, Budget, CostLineItem, ResourceInventory
from nimbrix_api.modules.ingestion.records import (
    CostRecord,
    ResourceRecord,
    RowError,
    derive_environment,
    derive_team,
)

BATCH_SIZE = 1_000
_COST_COMPARE = (
    "amount",
    "currency",
    "usage_quantity",
    "usage_unit",
    "tags",
    "resource_id",
    "region",
)


@dataclass
class UpsertCounts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0

    def add(self, other: UpsertCounts) -> None:
        self.inserted += other.inserted
        self.updated += other.updated
        self.unchanged += other.unchanged


def checksum_of(*parts: str | bytes) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part if isinstance(part, bytes) else part.encode())
        digest.update(b"\x1f")
    return digest.hexdigest()


def find_completed_duplicate(
    session: Session, tenant_id: uuid.UUID, source: str, checksum: str
) -> BillingImport | None:
    return session.scalar(
        select(BillingImport)
        .where(
            BillingImport.tenant_id == tenant_id,
            BillingImport.source == source,
            BillingImport.checksum == checksum,
            BillingImport.state.in_(("succeeded", "partially_failed")),
        )
        .order_by(BillingImport.created_at)
        .limit(1)
    )


def _cost_row(tenant_id: uuid.UUID, import_id: uuid.UUID, r: CostRecord) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "import_id": import_id,
        "provider": r.provider,
        "source_record_id": r.source_record_id,
        "account_id": r.account_id,
        "service": r.service,
        "region": r.region,
        "resource_id": r.resource_id,
        "usage_type": r.usage_type,
        "usage_start": r.usage_start,
        "usage_end": r.usage_end,
        "usage_date": r.usage_date,
        "amount": r.amount,
        "currency": r.currency,
        "usage_quantity": r.usage_quantity,
        "usage_unit": r.usage_unit,
        "tags": r.tags,
        "environment": derive_environment(r.tags),
        "team": derive_team(r.tags),
        "provenance": {**r.provenance, "import_id": str(import_id)},
    }


def upsert_cost_records(
    session: Session,
    tenant_id: uuid.UUID,
    import_id: uuid.UUID,
    records: Sequence[CostRecord],
    on_batch: Callable[[], None] | None = None,
) -> UpsertCounts:
    """Insert new line items; update changed ones (late-arriving corrections); count the rest."""
    counts = UpsertCounts()
    for offset in range(0, len(records), BATCH_SIZE):
        batch = records[offset : offset + BATCH_SIZE]
        # Deduplicate within the batch: the last occurrence of a source ID wins.
        rows = list(
            {r.source_record_id: _cost_row(tenant_id, import_id, r) for r in batch}.values()
        )
        stmt = insert(CostLineItem).values(rows)
        excluded = stmt.excluded
        changed = or_(
            *[
                getattr(CostLineItem, c).is_distinct_from(getattr(excluded, c))
                for c in _COST_COMPARE
            ]
        )
        upsert = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "provider", "source_record_id"],
            set_={
                **{
                    c: getattr(excluded, c)
                    for c in (
                        *_COST_COMPARE,
                        "usage_type",
                        "usage_start",
                        "usage_end",
                        "usage_date",
                        "environment",
                        "team",
                        "account_id",
                        "service",
                    )
                },
                "import_id": excluded.import_id,
                "provenance": excluded.provenance,
                "updated_at": datetime.now(UTC),
            },
            where=changed,
        ).returning(CostLineItem.created_at, CostLineItem.updated_at, CostLineItem.import_id)
        returned = session.execute(upsert).all()
        written_inserted = sum(1 for r in returned if r.created_at == r.updated_at)
        written_updated = len(returned) - written_inserted
        counts.inserted += written_inserted
        counts.updated += written_updated
        counts.unchanged += len(rows) - len(returned)
        session.flush()
        if on_batch:
            on_batch()
    return counts


def upsert_resources(
    session: Session,
    tenant_id: uuid.UUID,
    import_id: uuid.UUID | None,
    resources: Iterable[ResourceRecord],
    source: str,
) -> int:
    rows = [
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "provider": r.provider,
            "account_id": r.account_id,
            "region": r.region,
            "resource_id": r.resource_id,
            "resource_type": r.resource_type,
            "name": r.name,
            "state": r.state,
            "tags": r.tags,
            "environment": derive_environment(r.tags),
            "team": derive_team(r.tags),
            "attributes": r.attributes,
            "hourly_cost": r.hourly_cost,
            "monthly_cost": r.monthly_cost,
            "currency": r.currency,
            "utilization": r.utilization,
            "observed_at": r.observed_at,
            "source": source,
            "import_id": import_id,
        }
        for r in resources
    ]
    if not rows:
        return 0
    stmt = insert(ResourceInventory).values(rows)
    excluded = stmt.excluded
    keep = ("id", "tenant_id", "provider", "resource_id", "created_at")
    upsert = stmt.on_conflict_do_update(
        index_elements=["tenant_id", "provider", "resource_id"],
        set_={
            **{k: getattr(excluded, k) for k in rows[0] if k not in keep},
            "updated_at": datetime.now(UTC),
        },
        # Never let an older observation overwrite a newer one (late-arriving inventory).
        where=ResourceInventory.observed_at <= excluded.observed_at,
    ).returning(ResourceInventory.id)
    return len(session.execute(upsert).all())


def upsert_budgets(
    session: Session, tenant_id: uuid.UUID, budgets: Iterable[dict[str, Any]]
) -> int:
    count = 0
    for spec in budgets:
        existing = session.scalar(
            select(Budget).where(Budget.tenant_id == tenant_id, Budget.name == spec["name"])
        )
        if existing is None:
            session.add(
                Budget(
                    tenant_id=tenant_id,
                    name=spec["name"],
                    scope_type=spec["scope_type"],
                    scope_value=spec.get("scope_value"),
                    amount=spec["amount"],
                    currency=spec.get("currency", "USD"),
                    alert_thresholds=spec.get("alert_thresholds", [0.8, 1.0]),
                    owner=spec.get("owner"),
                )
            )
            count += 1
    session.flush()
    return count


def finish_import(
    session: Session,
    billing_import: BillingImport,
    *,
    received: int,
    counts: UpsertCounts,
    errors: list[RowError],
    rejected: int,
    resources: int = 0,
    details: dict[str, Any] | None = None,
) -> None:
    accepted = counts.inserted + counts.updated + counts.unchanged
    if received == 0 and rejected == 0:
        state = "failed"
        errors = [
            RowError(row=None, code="empty_dataset", message="the source contained no records")
        ]
    elif accepted == 0:
        state = "failed"
    elif rejected:
        state = "partially_failed"
    else:
        state = "succeeded"
    billing_import.state = state
    billing_import.rows_received = received
    billing_import.rows_inserted = counts.inserted
    billing_import.rows_updated = counts.updated
    billing_import.rows_unchanged = counts.unchanged
    billing_import.rows_rejected = rejected
    billing_import.resources_upserted = resources
    billing_import.errors = [e.as_dict() for e in errors]
    billing_import.details = {**(billing_import.details or {}), **(details or {})}
    billing_import.finished_at = datetime.now(UTC)
    record_audit(
        session,
        event_type=f"import.{state}",
        tenant_id=billing_import.tenant_id,
        actor_type="system",
        target_type="billing_import",
        target_id=billing_import.id,
        outcome="success" if state in ("succeeded", "partially_failed") else "failure",
        payload={
            "source": billing_import.source,
            "received": received,
            "inserted": counts.inserted,
            "updated": counts.updated,
            "unchanged": counts.unchanged,
            "rejected": rejected,
        },
    )
