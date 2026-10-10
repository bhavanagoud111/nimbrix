from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ForecastPoint(BaseModel):
    date: date
    yhat: Decimal
    lower: Decimal
    upper: Decimal


class ForecastOut(BaseModel):
    id: uuid.UUID
    scope_type: str
    scope_value: str
    status: str = Field(description="succeeded | insufficient_data | failed")
    message: str | None
    model_name: str
    model_version: str
    feature_version: str
    horizon_days: int
    interval_level: float
    training_start: date | None
    training_end: date | None = Field(description="Last actual day used (source data window end)")
    currency: str
    points: list[ForecastPoint]
    metrics: dict[str, Any]
    created_at: datetime


class ForecastList(BaseModel):
    items: list[ForecastOut]


class ForecastRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_type: Literal["total", "service", "account", "team", "environment", "region"] = "total"
    scope_value: str = Field(default="total", max_length=256)
    horizon_days: Literal[7, 30] = 30
    interval_level: float = Field(default=0.8, ge=0.5, le=0.99)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")


class AnomalyOut(BaseModel):
    id: uuid.UUID
    detector_version: str
    scope_type: str
    scope_value: str
    window_start: date
    window_end: date
    expected: Decimal
    observed: Decimal
    currency: str
    score: float
    severity: str
    status: str
    evidence: dict[str, Any]
    first_detected_at: datetime
    last_detected_at: datetime
    created_at: datetime


class AnomalyList(BaseModel):
    items: list[AnomalyOut]
    next_cursor: str | None


class AnomalyStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["open", "acknowledged", "resolved", "dismissed"]
    note: str | None = Field(default=None, max_length=1000)
