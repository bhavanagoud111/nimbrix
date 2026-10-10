from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ActionOut(BaseModel):
    name: str
    description: str
    mode: str
    payload_schema: dict[str, Any]


class ActionList(BaseModel):
    items: list[ActionOut]
    live_actions_enabled: bool = False


class ApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: str = Field(max_length=64)
    payload: dict[str, Any]
    recommendation_id: uuid.UUID | None = None


class PolicyPreview(BaseModel):
    action_type: str
    allowed: bool
    checks: list[dict[str, Any]]
    denial_reasons: list[str]
    payload: dict[str, Any] | None
    payload_hash: str | None
    resource: dict[str, Any] | None
    impact_preview: dict[str, Any]
    rollback_plan: dict[str, Any]


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", description="Hash of the payload you reviewed"
    )
    reason: str | None = Field(default=None, max_length=2000)


class RejectDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=2000)


class ApprovalOut(BaseModel):
    id: uuid.UUID
    action_type: str
    payload: dict[str, Any]
    payload_hash: str
    target_resource_id: str
    recommendation_id: uuid.UUID | None
    requested_by: uuid.UUID
    requested_by_email: str | None = None
    decided_by: uuid.UUID | None
    decided_by_email: str | None = None
    decision_reason: str | None
    status: str
    policy_result: dict[str, Any]
    impact_preview: dict[str, Any]
    rollback_plan: dict[str, Any]
    expires_at: datetime
    decided_at: datetime | None
    created_at: datetime
    execution_id: uuid.UUID | None = None


class ApprovalList(BaseModel):
    items: list[ApprovalOut]
    next_cursor: str | None


class RemediationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_request_id: uuid.UUID


class RemediationOut(BaseModel):
    id: uuid.UUID
    approval_request_id: uuid.UUID
    action_type: str
    mode: str = Field(description="Always 'simulation': no cloud write is possible")
    dry_run: bool
    state: str
    attempts: int
    result: dict[str, Any]
    verification: dict[str, Any]
    rollback_plan: dict[str, Any]
    error: str | None
    executed_by: uuid.UUID
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RemediationList(BaseModel):
    items: list[RemediationOut]
    next_cursor: str | None
