"""Opt-in demo seeding: queue synthetic imports for the demo tenants (the worker loads them)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from nimbrix_api.core.audit import record_audit
from nimbrix_api.core.config import Settings
from nimbrix_api.models import Tenant, User
from nimbrix_api.modules.ingestion.requests import request_demo_import

logger = logging.getLogger("nimbrix.seed")

DEMO_IMPORTS = {"acme-demo": {"days": 120, "seed": 42}, "globex-demo": {"days": 60, "seed": 7}}


def seed_demo_data(session: Session, settings: Settings, tenants: dict[str, Tenant]) -> None:
    end_date = datetime.now(UTC).date() - timedelta(days=1)
    for slug, spec in DEMO_IMPORTS.items():
        tenant = tenants[slug]
        admin = session.scalar(select(User).where(User.email == f"admin@{slug.split('-')[0]}.demo"))
        requested = request_demo_import(
            session,
            tenant_id=tenant.id,
            user_id=admin.id if admin else None,
            end_date=end_date,
            days=spec["days"],
            seed=spec["seed"],
        )
        record_audit(
            session,
            event_type="import.requested",
            tenant_id=tenant.id,
            actor_type="system",
            actor_label="nimbrix-seed",
            target_type="billing_import",
            target_id=requested.billing_import.id,
            payload={"source": "demo", "duplicate": requested.duplicate},
        )
        logger.info(
            "demo_import_queued",
            extra={"tenant": slug, "duplicate": requested.duplicate, "end_date": str(end_date)},
        )
    session.commit()
