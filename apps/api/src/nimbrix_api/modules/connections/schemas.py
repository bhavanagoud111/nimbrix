from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ACCOUNT_ID = r"^\d{12}$"
ROLE_ARN = re.compile(r"^arn:aws(-[a-z]+)?:iam::(\d{12}):role/[\w+=,.@/-]{1,512}$")
REGION = re.compile(r"^[a-z]{2}(-gov)?-[a-z]+-\d$")
# Anything that looks like an AWS access key ID or secret key is refused outright.
_KEY_LIKE = re.compile(r"(AKIA|ASIA)[A-Z0-9]{16}|[A-Za-z0-9/+=]{40}")


def _reject_key_material(value: str | None) -> str | None:
    if value and _KEY_LIKE.search(value):
        raise ValueError("looks like an access key; Nimbrix never accepts raw credentials")
    return value


class ConnectionCreate(BaseModel):
    """Connection metadata only. There are deliberately no credential fields."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["aws"] = "aws"
    name: str = Field(min_length=1, max_length=200)
    account_id: str = Field(pattern=ACCOUNT_ID)
    role_arn: str | None = Field(default=None, max_length=2048)
    external_id: str | None = Field(default=None, min_length=2, max_length=1224)
    regions: list[str] = Field(default_factory=lambda: ["us-east-1"], min_length=1, max_length=30)

    @field_validator("role_arn")
    @classmethod
    def _role_arn(cls, value: str | None) -> str | None:
        if value is not None and not ROLE_ARN.match(value):
            raise ValueError("must be an IAM role ARN (arn:aws:iam::<account>:role/<name>)")
        return value

    @field_validator("external_id", "name")
    @classmethod
    def _no_keys(cls, value: str | None) -> str | None:
        return _reject_key_material(value)

    @field_validator("regions")
    @classmethod
    def _regions(cls, value: list[str]) -> list[str]:
        for region in value:
            if not REGION.match(region):
                raise ValueError("invalid AWS region name")
        return sorted(set(value))


class ConnectionOut(BaseModel):
    id: uuid.UUID
    provider: str
    name: str
    account_id: str
    role_arn: str | None
    has_external_id: bool
    regions: list[str]
    access_mode: str
    status: str
    last_validated_at: datetime | None
    last_error: str | None
    created_at: datetime


class ConnectionList(BaseModel):
    items: list[ConnectionOut]
