"""Cost analytics: summary, time series, breakdowns, unit costs, filter dimensions."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from nimbrix_api.api.v1.paths import API_V1_PREFIX
from nimbrix_api.core.deps import AuthContext, DbSession, require
from nimbrix_api.core.permissions import Permission
from nimbrix_api.modules.costs import service
from nimbrix_api.modules.costs.schemas import (
    CostBreakdown,
    CostSummary,
    CostTimeseries,
    Dimensions,
    UnitCosts,
)

router = APIRouter(prefix=API_V1_PREFIX, tags=["costs"])
DimensionParam = Literal["account", "service", "region", "environment", "team", "resource"]


def cost_filters(
    start: Annotated[date | None, Query(description="Inclusive UTC start date")] = None,
    end: Annotated[
        date | None, Query(description="Inclusive UTC end date (default: latest complete day)")
    ] = None,
    account_id: Annotated[str | None, Query(max_length=32)] = None,
    service_name: Annotated[str | None, Query(alias="service", max_length=128)] = None,
    region: Annotated[str | None, Query(max_length=32)] = None,
    environment: Annotated[
        str | None, Query(max_length=64, description="'(unallocated)' for untagged")
    ] = None,
    team: Annotated[
        str | None, Query(max_length=128, description="'(unallocated)' for untagged")
    ] = None,
    tag_key: Annotated[str | None, Query(max_length=128)] = None,
    tag_value: Annotated[str | None, Query(max_length=256)] = None,
    currency: Annotated[str, Query(pattern=r"^[A-Z]{3}$")] = "USD",
) -> service.CostFilters:
    return service.CostFilters(
        start=start,
        end=end,
        account_id=account_id,
        service=service_name,
        region=region,
        environment=environment,
        team=team,
        tag_key=tag_key,
        tag_value=tag_value,
        currency=currency,
    )


Filters = Annotated[service.CostFilters, Depends(cost_filters)]
Reader = Annotated[AuthContext, Depends(require(Permission.READ))]


@router.get(
    "/costs/summary", response_model=CostSummary, summary="Totals, comparisons, allocation, drivers"
)
def cost_summary(auth: Reader, session: DbSession, filters: Filters) -> CostSummary:
    data = service.summary(session, auth.tenant_id, filters)
    return CostSummary(**data, data_mode=service.data_mode(session, auth.tenant_id))


@router.get(
    "/costs/timeseries", response_model=CostTimeseries, summary="Cost over time, optionally grouped"
)
def cost_timeseries(
    auth: Reader,
    session: DbSession,
    filters: Filters,
    granularity: Literal["day", "month"] = "day",
    group_by: DimensionParam | None = None,
    top: Annotated[int, Query(ge=1, le=20)] = 8,
) -> CostTimeseries:
    return CostTimeseries(
        **service.timeseries(
            session, auth.tenant_id, filters, granularity=granularity, group_by=group_by, top=top
        )
    )


@router.get(
    "/costs/breakdown",
    response_model=CostBreakdown,
    summary="Cost by one dimension with prior-period change",
)
def cost_breakdown(
    auth: Reader,
    session: DbSession,
    filters: Filters,
    dimension: DimensionParam = "service",
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> CostBreakdown:
    return CostBreakdown(**service.breakdown(session, auth.tenant_id, filters, dimension, limit))


@router.get("/costs/unit-costs", response_model=UnitCosts, summary="Cost per provider usage unit")
def cost_unit_costs(auth: Reader, session: DbSession, filters: Filters) -> UnitCosts:
    return UnitCosts(**service.unit_costs(session, auth.tenant_id, filters))


@router.get(
    "/costs/dimensions", response_model=Dimensions, summary="Filter values present in the data"
)
def cost_dimensions(auth: Reader, session: DbSession) -> Dimensions:
    return Dimensions(**service.dimension_values(session, auth.tenant_id))
