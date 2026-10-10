from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DemoImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    end_date: date | None = Field(
        default=None, description="Last day of synthetic data (UTC). Defaults to yesterday."
    )
    days: int = Field(default=120, ge=30, le=400)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)


class JobOut(BaseModel):
    id: uuid.UUID
    kind: str
    state: str
    attempts: int
    max_attempts: int
    run_after: datetime
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ImportOut(BaseModel):
    id: uuid.UUID
    source: str
    state: str
    mode: str = Field(description="'demo' for synthetic data, 'live' for real provider data")
    checksum: str | None
    filename: str | None
    period_start: date | None
    period_end: date | None
    rows_received: int
    rows_inserted: int
    rows_updated: int
    rows_unchanged: int
    rows_rejected: int
    resources_upserted: int
    errors: list[dict[str, Any]]
    details: dict[str, Any]
    duplicate_of: uuid.UUID | None
    job_id: uuid.UUID | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ImportList(BaseModel):
    items: list[ImportOut]
    next_cursor: str | None


class AwsValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: uuid.UUID
    check_cost_explorer: bool = Field(
        default=False,
        description="Also call ce:GetCostAndUsage once. AWS bills each Cost Explorer API request.",
    )


class ValidationCheckOut(BaseModel):
    name: str
    passed: bool
    detail: str


class AwsValidateResponse(BaseModel):
    connection_id: uuid.UUID
    status: str
    checks: list[ValidationCheckOut]
    credentials_persisted: bool = Field(
        default=False, description="Always false: temporary credentials are never stored"
    )


class AwsSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date = Field(description="Exclusive end date")
    include_inventory: bool = True


class AwsSyncResponse(BaseModel):
    import_id: uuid.UUID
    jobs: list[JobOut]
