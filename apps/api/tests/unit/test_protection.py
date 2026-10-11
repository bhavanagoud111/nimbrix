"""Security headers, body-size limits, and rate limiting."""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from nimbrix_api.core.config import Settings
from nimbrix_api.core.protection import SlidingWindowLimiter
from nimbrix_api.db.session import Database
from nimbrix_api.main import create_app
from tests.conftest import UNUSED_DB_URL


def _client(make_settings: Callable[..., Settings], **overrides: object) -> TestClient:
    return TestClient(
        create_app(make_settings(**overrides), Database(create_engine(UNUSED_DB_URL))),
        raise_server_exceptions=False,
    )


def test_security_headers_on_api_responses(client: TestClient) -> None:
    headers = client.get("/api/v1/health/live").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Content-Security-Policy"].startswith("default-src 'none'")
    assert headers["Cache-Control"] == "no-store"
    assert "Strict-Transport-Security" not in headers
    assert (
        "Content-Security-Policy" not in client.get("/api/v1/docs").headers
    )  # Swagger UI needs scripts


def test_hsts_only_in_production(make_settings: Callable[..., Settings]) -> None:
    client = _client(make_settings, environment="production", demo_auth_enabled=False)
    assert (
        client.get("/api/v1/health/live")
        .headers["Strict-Transport-Security"]
        .startswith("max-age=")
    )


def test_body_size_limit(make_settings: Callable[..., Settings]) -> None:
    client = _client(make_settings, max_request_bytes=2048)
    declared = client.post(
        "/api/v1/auth/login", content=b"x" * 4096, headers={"content-type": "application/json"}
    )
    assert declared.status_code == 413
    assert declared.json()["error"]["code"] == "request_too_large"

    def chunks() -> Iterator[bytes]:  # no Content-Length: streamed body
        for _ in range(4):
            yield b"y" * 1024

    streamed = client.post(
        "/api/v1/auth/login", content=chunks(), headers={"content-type": "application/json"}
    )
    assert streamed.status_code == 413


def test_login_rate_limit(make_settings: Callable[..., Settings]) -> None:
    client = _client(make_settings, rate_limit_login_per_minute=3)
    codes = [
        client.post("/api/v1/auth/login", json={"email": "a@b.c", "password": "x"}).status_code
        for _ in range(5)
    ]
    assert codes[:3] != [429, 429, 429]
    assert codes[3:] == [429, 429]
    limited = client.post("/api/v1/auth/login", json={"email": "a@b.c", "password": "x"})
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1
    assert client.get("/api/v1/health/live").status_code == 200  # health is exempt


def test_general_rate_limit_is_per_session(make_settings: Callable[..., Settings]) -> None:
    client = _client(make_settings, rate_limit_requests_per_minute=2)
    a = {"Authorization": "Bearer session-a"}
    b = {"Authorization": "Bearer session-b"}
    assert [client.get("/api/v1/me", headers=a).status_code for _ in range(3)][-1] == 429
    assert client.get("/api/v1/me", headers=b).status_code != 429


def test_sliding_window() -> None:
    limiter = SlidingWindowLimiter(limit=2, window_seconds=10)
    assert limiter.hit("k", now=0) is None
    assert limiter.hit("k", now=1) is None
    assert limiter.hit("k", now=2) == 8
    assert limiter.hit("k", now=10.5) is None  # first hit expired
