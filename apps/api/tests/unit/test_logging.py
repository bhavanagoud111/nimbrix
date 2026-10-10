from __future__ import annotations

import json
import logging

import pytest

from nimbrix_api.core.logging import JsonFormatter, redact
from nimbrix_api.core.request_context import _request_id


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("postgresql+psycopg://nimbrix:hunter2@db:5432/nimbrix", "hunter2"),
        ("password=hunter2 host=db", "hunter2"),
        ("api_key: sk-live-123", "sk-live-123"),
        ("Authorization=Bearer-abc", "Bearer-abc"),
        ("aws access-key = AKIAEXAMPLE", "AKIAEXAMPLE"),
    ],
)
def test_redact_removes_secrets(raw: str, leaked: str) -> None:
    redacted = redact(raw)
    assert leaked not in redacted
    assert "***" in redacted


def test_redact_keeps_ordinary_text() -> None:
    assert redact("imported 42 rows for account 1234") == "imported 42 rows for account 1234"


def test_json_formatter_includes_request_id_and_redacts_extras() -> None:
    record = logging.LogRecord(
        "nimbrix.test", logging.INFO, __file__, 1, "connecting to %s", ("x://u:pw@h",), None
    )
    record.dsn = "postgresql://u:topsecret@h/db"
    token = _request_id.set("req-12345678")
    try:
        payload = json.loads(JsonFormatter().format(record))
    finally:
        _request_id.reset(token)
    assert payload["request_id"] == "req-12345678"
    assert payload["message"] == "connecting to x://u:***@h"
    assert "topsecret" not in payload["dsn"]
    assert payload["level"] == "INFO"
