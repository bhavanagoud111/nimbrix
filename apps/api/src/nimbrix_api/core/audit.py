"""Append-only audit event writer with payload redaction."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from nimbrix_api.core.logging import redact
from nimbrix_api.core.request_context import get_request_id
from nimbrix_api.models import AuditEvent

_SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "access_key",
)
_MAX_STRING = 2_000


def redact_payload(value: Any) -> Any:
    """Recursively mask sensitive keys and scrub secrets embedded in strings."""
    if isinstance(value, dict):
        return {
            str(k): "***"
            if any(part in str(k).lower() for part in _SENSITIVE_KEY_PARTS)
            else redact_payload(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact_payload(v) for v in value]
    if isinstance(value, str):
        return redact(value)[:_MAX_STRING]
    if isinstance(value, int | float | bool) or value is None:
        return value
    return redact(str(value))[:_MAX_STRING]


def record_audit(
    session: Session,
    *,
    event_type: str,
    tenant_id: uuid.UUID | None,
    actor_type: str = "user",
    actor_id: uuid.UUID | None = None,
    actor_label: str | None = None,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    outcome: str = "success",
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    """Stage an audit event in the caller's transaction (committed with the change it audits)."""
    event = AuditEvent(
        tenant_id=tenant_id,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=actor_label,
        event_type=event_type,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        outcome=outcome,
        request_id=get_request_id(),
        payload=redact_payload(payload or {}),
    )
    session.add(event)
    return event
