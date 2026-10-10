"""Tenant-scoped lookups. Rows from other tenants are indistinguishable from missing ones."""

from __future__ import annotations

import uuid

from sqlalchemy import exists, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from nimbrix_api.core.audit import record_audit
from nimbrix_api.core.deps import AuthContext
from nimbrix_api.core.errors import ApiError


def get_owned[T](
    session: Session,
    model: type[T],
    auth: AuthContext,
    object_id: uuid.UUID,
    *,
    label: str,
    for_update: bool = False,
) -> T:
    id_col: InstrumentedAttribute[uuid.UUID] = model.id  # type: ignore[attr-defined]
    tenant_col: InstrumentedAttribute[uuid.UUID] = model.tenant_id  # type: ignore[attr-defined]
    stmt = select(model).where(id_col == object_id, tenant_col == auth.tenant_id)
    if for_update:
        stmt = stmt.with_for_update()
    obj = session.scalar(stmt)
    if obj is not None:
        return obj
    if session.scalar(select(exists().where(id_col == object_id))):
        # The ID exists under another tenant: deny exactly like "missing", but audit it.
        record_audit(
            session,
            event_type="security.cross_tenant_access_denied",
            tenant_id=auth.tenant_id,
            actor_id=auth.user_id,
            actor_label=auth.email,
            target_type=label,
            target_id=object_id,
            outcome="denied",
        )
        session.commit()
    raise ApiError(404, "not_found", f"{label.replace('_', ' ').capitalize()} not found")
