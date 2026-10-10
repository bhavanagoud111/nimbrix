"""Demo data seeding (implemented in Phase 2: synthetic ingestion)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from nimbrix_api.core.config import Settings
from nimbrix_api.models import Tenant


def seed_demo_data(session: Session, settings: Settings, tenants: dict[str, Tenant]) -> None:
    raise NotImplementedError("Synthetic demo data seeding arrives in Phase 2")
