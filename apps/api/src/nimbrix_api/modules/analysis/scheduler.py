"""Daily analysis scheduling without duplicate work.

The worker calls ``schedule_daily_analysis`` periodically. A tenant gets at most one active
analysis job (dedupe key) and at most one scheduled run per UTC day.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from nimbrix_api.models import CostLineItem, Job, Tenant
from nimbrix_api.modules.jobs import queue


def schedule_daily_analysis(session: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    day_start = datetime.combine(now.date(), time(0), tzinfo=UTC)
    scheduled = 0
    tenants = session.scalars(
        select(Tenant.id).where(exists().where(CostLineItem.tenant_id == Tenant.id))
    ).all()
    for tenant_id in tenants:
        ran_today = session.scalar(
            select(
                exists().where(
                    Job.tenant_id == tenant_id,
                    Job.kind == "analyze_tenant",
                    Job.created_at >= day_start,
                    Job.state.in_(("queued", "running", "succeeded")),
                )
            )
        )
        if ran_today:
            continue
        queue.enqueue(
            session,
            tenant_id=tenant_id,
            kind="analyze_tenant",
            payload={"reason": "daily_schedule", "date": str(now.date())},
            dedupe_key=f"analyze:{tenant_id}",
        )
        scheduled += 1
    session.commit()
    return scheduled
