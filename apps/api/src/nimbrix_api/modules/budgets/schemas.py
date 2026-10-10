from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BudgetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    scope_type: Literal["total", "account", "service", "team", "environment"]
    scope_value: str | None = Field(default=None, max_length=256)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    alert_thresholds: list[Decimal] = Field(
        default_factory=lambda: [Decimal("0.8"), Decimal("1.0")], min_length=1, max_length=5
    )
    owner: str | None = Field(default=None, max_length=128)

    @field_validator("alert_thresholds")
    @classmethod
    def _thresholds(cls, value: list[Decimal]) -> list[Decimal]:
        if any(t <= 0 or t > 2 for t in value):
            raise ValueError("thresholds are utilization ratios in (0, 2]")
        return sorted(set(value))


class BudgetOut(BaseModel):
    id: uuid.UUID
    name: str
    scope_type: str
    scope_value: str | None
    period: str
    amount: Decimal
    currency: str
    alert_thresholds: list[Decimal]
    owner: str | None
    period_start: date
    period_end: date
    as_of: date | None
    actual: Decimal
    projected: Decimal | None
    projection_method: str | None = Field(description="forecast | run_rate")
    utilization: Decimal
    projected_utilization: Decimal | None
    status: str = Field(description="on_track | warning | at_risk | over_budget")


class BudgetList(BaseModel):
    items: list[BudgetOut]


class BudgetAlertOut(BaseModel):
    id: uuid.UUID
    budget_id: uuid.UUID
    budget_name: str
    period_start: date
    threshold: Decimal
    kind: str
    utilization: Decimal
    amount: Decimal
    currency: str
    status: str
    details: dict[str, Any]
    created_at: datetime


class BudgetAlertList(BaseModel):
    items: list[BudgetAlertOut]
