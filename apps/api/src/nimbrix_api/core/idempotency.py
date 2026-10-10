"""Idempotent mutations: same key + same request => same stored response."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from nimbrix_api.core.deps import AuthContext
from nimbrix_api.core.errors import ApiError
from nimbrix_api.models import IdempotencyRecord

RETENTION = timedelta(hours=24)
REPLAY_HEADER = "Idempotent-Replayed"


def fingerprint(method: str, path: str, body: Any) -> str:
    canonical = json.dumps(
        {"method": method, "path": path, "body": body}, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class IdempotencyGuard:
    """Use per mutation: `replay()` first; after the work succeeds, `remember()` then commit."""

    def __init__(
        self, session: Session, auth: AuthContext, key: str, method: str, path: str, body: Any
    ) -> None:
        self.session = session
        self.auth = auth
        self.key = key
        self.method = method
        self.path = path
        self.request_hash = fingerprint(method, path, body)

    def replay(self) -> JSONResponse | None:
        now = datetime.now(UTC)
        record = self.session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.tenant_id == self.auth.tenant_id,
                IdempotencyRecord.user_id == self.auth.user_id,
                IdempotencyRecord.key == self.key,
            )
        )
        if record is None:
            return None
        if record.expires_at <= now:
            self.session.delete(record)
            self.session.flush()
            return None
        if record.request_hash != self.request_hash:
            raise ApiError(
                409,
                "idempotency_key_reused",
                "This Idempotency-Key was already used for a different request",
            )
        if record.status_code is None or record.response_body is None:
            raise ApiError(409, "request_in_progress", "A request with this key is in progress")
        return JSONResponse(
            status_code=record.status_code,
            content=record.response_body,
            headers={REPLAY_HEADER: "true"},
        )

    def remember(self, status_code: int, body: BaseModel) -> None:
        self.session.add(
            IdempotencyRecord(
                tenant_id=self.auth.tenant_id,
                user_id=self.auth.user_id,
                key=self.key,
                method=self.method,
                path=self.path,
                request_hash=self.request_hash,
                status_code=status_code,
                response_body=body.model_dump(mode="json"),
                expires_at=datetime.now(UTC) + RETENTION,
            )
        )
