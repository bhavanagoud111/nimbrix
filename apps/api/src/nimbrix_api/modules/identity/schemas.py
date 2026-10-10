from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from nimbrix_api.core.permissions import Permission


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    password: str = Field(min_length=1, max_length=256)
    tenant_slug: str | None = Field(default=None, max_length=63)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str


class TenantOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str


class MeResponse(BaseModel):
    user: UserOut
    tenant: TenantOut
    role: str
    permissions: list[Permission]
    auth_method: str = Field(
        description="How the caller authenticated. Demo sessions are not OIDC."
    )
    session_expires_at: datetime


class LoginResponse(MeResponse):
    csrf_token: str = Field(description="Send as X-CSRF-Token on cookie-authenticated mutations")
