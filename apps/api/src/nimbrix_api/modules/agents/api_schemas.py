from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InvestigateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str | None = Field(default=None, max_length=2000)


class AgentStepOut(BaseModel):
    step_index: int
    node: str
    agent_role: str
    status: str
    mode: str
    summary: str
    evidence_refs: list[str]
    tool_calls: list[dict[str, Any]]
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: Decimal | None
    latency_ms: float
    error: str | None
    created_at: datetime


class InvestigationOut(BaseModel):
    id: uuid.UUID
    anomaly_id: uuid.UUID | None
    state: str
    mode: str = Field(
        description="deterministic | anthropic | openai | ollama: what produced the analysis"
    )
    model: str | None
    trace_id: str
    question: str | None
    report: dict[str, Any] | None
    budget: dict[str, Any]
    usage: dict[str, Any]
    error: str | None
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    steps: list[AgentStepOut] = []


class InvestigationList(BaseModel):
    items: list[InvestigationOut]
    next_cursor: str | None
