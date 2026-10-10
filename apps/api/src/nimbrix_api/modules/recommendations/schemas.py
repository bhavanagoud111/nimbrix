from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RecommendationOut(BaseModel):
    id: uuid.UUID
    category: str
    rule_version: str
    title: str
    summary: str
    resource_id: str | None
    account_id: str | None
    region: str | None
    affected_resources: list[str]
    evidence: dict[str, Any]
    eligibility: list[dict[str, Any]]
    assumptions: list[str]
    savings_formula: str | None
    estimated_monthly_savings: Decimal | None = Field(
        description="Estimated list-price reduction per month. Never a realized saving."
    )
    currency: str
    confidence: str
    risk: str
    owner: str | None
    status: str
    utilization_window: dict[str, Any] | None
    proposed_action: str | None
    last_simulation: dict[str, Any] | None
    last_evaluated_at: datetime
    created_at: datetime


class RecommendationList(BaseModel):
    items: list[RecommendationOut]
    total_estimated_monthly_savings: Decimal
    currency: str
    next_cursor: str | None


class SimulationOut(BaseModel):
    recommendation_id: uuid.UUID
    mode: str = Field(description="Always 'simulation'")
    realized: bool = Field(description="Always false: nothing was changed")
    status: str
    message: str | None = None
    currency: str | None = None
    current_monthly_cost: Decimal | None = None
    projected_monthly_cost: Decimal | None = None
    estimated_monthly_savings: Decimal | None = None
    savings_range: dict[str, Decimal] | None = None
    basis: dict[str, Any] | None = None
    excluded_costs: list[dict[str, Any]] = []
    assumptions: list[str] = []
    confidence: str | None = None
    simulated_at: datetime


class DismissRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=1000)
