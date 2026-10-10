from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class WindowOut(BaseModel):
    start: date
    end: date
    days: int


class Driver(BaseModel):
    dimension: str
    key: str
    amount: Decimal
    previous_amount: Decimal
    delta: Decimal


class MonthToDate(BaseModel):
    start: date
    end: date
    amount: Decimal
    previous_start: date
    previous_end: date
    previous_amount: Decimal
    change_pct: Decimal | None
    days_in_month: int


class CurrencyTotal(BaseModel):
    currency: str
    total: Decimal


class CostSummary(BaseModel):
    has_data: bool
    currency: str
    data_mode: str = Field(description="demo | live | mixed | none")
    as_of: date | None = Field(default=None, description="Latest complete UTC day with data")
    window: WindowOut | None = None
    total: Decimal | None = None
    daily_average: Decimal | None = None
    previous_window: dict[str, date] | None = None
    previous_total: Decimal | None = None
    change_pct: Decimal | None = None
    allocated: Decimal | None = None
    unallocated: Decimal | None = None
    allocation_pct: Decimal | None = None
    month_to_date: MonthToDate | None = None
    top_drivers: list[Driver] = []
    other_currencies: list[CurrencyTotal] = Field(
        default=[], description="Spend in other currencies, never converted or added to total"
    )


class SeriesPoint(BaseModel):
    date: date
    amount: Decimal


class Series(BaseModel):
    key: str
    total: Decimal
    points: list[SeriesPoint]


class CostTimeseries(BaseModel):
    has_data: bool
    currency: str
    granularity: str | None = None
    group_by: str | None = None
    window: WindowOut | None = None
    partial_last_bucket: bool = False
    buckets: list[date] = []
    series: list[Series] = []


class BreakdownItem(BaseModel):
    key: str
    amount: Decimal
    share_pct: Decimal | None
    previous_amount: Decimal
    change_pct: Decimal | None


class CostBreakdown(BaseModel):
    has_data: bool
    currency: str
    dimension: str
    window: WindowOut | None = None
    total: Decimal | None = None
    items: list[BreakdownItem] = []


class UnitCost(BaseModel):
    service: str
    usage_unit: str
    amount: Decimal
    usage_quantity: Decimal
    unit_cost: Decimal | None


class UnitCosts(BaseModel):
    has_data: bool
    currency: str
    window: WindowOut | None = None
    items: list[UnitCost] = []
    note: str | None = None


class DataWindow(BaseModel):
    start: date | None
    end: date | None
    as_of: date | None


class Dimensions(BaseModel):
    account: list[str]
    service: list[str]
    region: list[str]
    environment: list[str]
    team: list[str]
    currencies: list[str]
    tag_keys: list[str]
    data_window: DataWindow
