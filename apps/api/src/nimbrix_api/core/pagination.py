"""Keyset (cursor) pagination over (timestamp, id) ordering, newest first."""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Select, and_, or_
from sqlalchemy.orm import InstrumentedAttribute, Session

from nimbrix_api.core.errors import ApiError

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class PageInfo(BaseModel):
    limit: int
    next_cursor: str | None = Field(description="Pass as `cursor` to fetch the next page")


def encode_cursor(sort_value: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps([sort_value.isoformat(), str(row_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        sort_raw, id_raw = json.loads(base64.urlsafe_b64decode(padded))
        return datetime.fromisoformat(sort_raw), uuid.UUID(id_raw)
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ApiError(400, "invalid_cursor", "The pagination cursor is invalid") from None


def paginate[T](
    session: Session,
    stmt: Select[T],
    sort_column: InstrumentedAttribute[datetime],
    id_column: InstrumentedAttribute[uuid.UUID],
    limit: int,
    cursor: str | None,
) -> tuple[Sequence[T], str | None]:
    limit = max(1, min(limit, MAX_LIMIT))
    if cursor:
        sort_value, row_id = decode_cursor(cursor)
        stmt = stmt.where(
            or_(sort_column < sort_value, and_(sort_column == sort_value, id_column < row_id))
        )
    rows = session.scalars(
        stmt.order_by(sort_column.desc(), id_column.desc()).limit(limit + 1)
    ).all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last: Any = rows[-1]
        next_cursor = encode_cursor(getattr(last, sort_column.key), getattr(last, id_column.key))
    return rows, next_cursor
