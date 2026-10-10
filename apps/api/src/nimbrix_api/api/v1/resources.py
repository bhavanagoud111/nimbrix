"""Resource inventory with utilization evidence."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select

from nimbrix_api.api.v1.paths import API_V1_PREFIX
from nimbrix_api.core.deps import AuthContext, DbSession, require
from nimbrix_api.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, paginate
from nimbrix_api.core.permissions import Permission
from nimbrix_api.core.tenancy import get_owned
from nimbrix_api.models import CostLineItem, ResourceInventory
from nimbrix_api.modules.resources.schemas import (
    DailyCost,
    ResourceDetail,
    ResourceList,
    ResourceOut,
)

router = APIRouter(prefix=API_V1_PREFIX, tags=["resources"])


def resource_out(r: ResourceInventory) -> ResourceOut:
    data = {c: getattr(r, c) for c in ResourceOut.model_fields if c != "mode"}
    return ResourceOut(**data, mode="demo" if r.source == "demo" else "live")


@router.get("/resources", response_model=ResourceList, summary="List inventory")
def list_resources(
    auth: Annotated[AuthContext, Depends(require(Permission.READ))],
    session: DbSession,
    resource_type: Annotated[str | None, Query(max_length=64)] = None,
    account_id: Annotated[str | None, Query(max_length=32)] = None,
    region: Annotated[str | None, Query(max_length=32)] = None,
    environment: Annotated[str | None, Query(max_length=64)] = None,
    team: Annotated[str | None, Query(max_length=128)] = None,
    state: Annotated[str | None, Query(max_length=32)] = None,
    q: Annotated[
        str | None, Query(max_length=100, description="Search name or resource ID")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> ResourceList:
    stmt = select(ResourceInventory).where(ResourceInventory.tenant_id == auth.tenant_id)
    filters = {
        ResourceInventory.resource_type: resource_type,
        ResourceInventory.account_id: account_id,
        ResourceInventory.region: region,
        ResourceInventory.environment: environment,
        ResourceInventory.team: team,
        ResourceInventory.state: state,
    }
    for column, value in filters.items():
        if value:
            stmt = stmt.where(column == value)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                ResourceInventory.name.ilike(pattern, escape="\\"),
                ResourceInventory.resource_id.ilike(pattern, escape="\\"),
            )
        )
    rows, next_cursor = paginate(
        session, stmt, ResourceInventory.created_at, ResourceInventory.id, limit, cursor
    )
    return ResourceList(items=[resource_out(r) for r in rows], next_cursor=next_cursor)


@router.get(
    "/resources/{resource_uuid}", response_model=ResourceDetail, summary="Resource with recent cost"
)
def get_resource(
    resource_uuid: uuid.UUID,
    auth: Annotated[AuthContext, Depends(require(Permission.READ))],
    session: DbSession,
) -> ResourceDetail:
    resource = get_owned(session, ResourceInventory, auth, resource_uuid, label="resource")
    latest = session.scalar(
        select(func.max(CostLineItem.usage_date)).where(
            CostLineItem.tenant_id == auth.tenant_id,
            CostLineItem.resource_id == resource.resource_id,
        )
    )
    daily: list[DailyCost] = []
    if latest is not None:
        rows = session.execute(
            select(CostLineItem.usage_date, func.sum(CostLineItem.amount))
            .where(
                CostLineItem.tenant_id == auth.tenant_id,
                CostLineItem.resource_id == resource.resource_id,
                CostLineItem.usage_date > latest - timedelta(days=30),
                CostLineItem.currency == resource.currency,
            )
            .group_by(CostLineItem.usage_date)
            .order_by(CostLineItem.usage_date)
        ).all()
        daily = [DailyCost(date=d, amount=a) for d, a in rows]
    return ResourceDetail(
        **resource_out(resource).model_dump(),
        cost_last_30_days=sum((d.amount for d in daily), Decimal(0)),
        cost_currency=resource.currency,
        daily_cost=daily,
    )
