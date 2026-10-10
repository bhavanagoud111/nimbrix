from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class ResourceOut(BaseModel):
    id: uuid.UUID
    provider: str
    account_id: str
    region: str
    resource_id: str
    resource_type: str
    name: str | None
    state: str
    environment: str | None
    team: str | None
    tags: dict[str, Any]
    attributes: dict[str, Any]
    hourly_cost: Decimal | None
    monthly_cost: Decimal | None
    currency: str
    utilization: dict[str, Any] | None
    observed_at: datetime
    source: str
    mode: str


class ResourceList(BaseModel):
    items: list[ResourceOut]
    next_cursor: str | None


class DailyCost(BaseModel):
    date: date
    amount: Decimal


class ResourceDetail(ResourceOut):
    cost_last_30_days: Decimal
    cost_currency: str
    daily_cost: list[DailyCost]
