"""Tracing emits route-level spans without secrets, and logs carry trace IDs."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine

from nimbrix_api.core import telemetry
from nimbrix_api.core.config import Settings
from nimbrix_api.core.logging import JsonFormatter
from nimbrix_api.db.session import Database
from nimbrix_api.main import create_app
from tests.conftest import UNUSED_DB_URL

EXPORTER = InMemorySpanExporter()


def test_spans_have_routes_but_no_secrets(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    telemetry.setup_tracing(settings, processor=SimpleSpanProcessor(EXPORTER))
    app = create_app(settings, Database(create_engine(UNUSED_DB_URL)))
    telemetry.setup_tracing(settings, app=app, processor=SimpleSpanProcessor(EXPORTER))
    client = TestClient(app, raise_server_exceptions=False)
    client.post(
        "/api/v1/auth/login",
        json={"email": "spy@example.com", "password": "super-secret-pw"},
        headers={
            "Authorization": "Bearer token-should-not-leak",
            "Cookie": "nimbrix_session=cookie-secret",
        },
    )
    client.get("/api/v1/health/live")  # excluded from tracing
    spans = EXPORTER.get_finished_spans()
    names = {s.name for s in spans}
    assert any("/api/v1/auth/login" in n for n in names)
    assert not any("/health/" in n for n in names)
    dumped = json.dumps([dict(s.attributes or {}) for s in spans], default=str)
    for secret in ("super-secret-pw", "token-should-not-leak", "cookie-secret", "spy@example.com"):
        assert secret not in dumped


def test_logs_include_trace_ids_inside_spans(make_settings: Callable[..., Settings]) -> None:
    telemetry.setup_tracing(make_settings(), processor=SimpleSpanProcessor(EXPORTER))
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", None, None)
    with telemetry.tracer.start_as_current_span("unit"):
        payload = json.loads(JsonFormatter().format(record))
    assert len(payload["trace_id"]) == 32
    assert len(payload["span_id"]) == 16
    outside = json.loads(JsonFormatter().format(record))
    assert "trace_id" not in outside
