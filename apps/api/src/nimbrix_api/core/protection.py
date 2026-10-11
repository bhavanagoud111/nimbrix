"""Abuse protection and response hardening: security headers, body size limit, rate limits.

Rate limiting is in-process (per API replica) using a sliding window. It protects a single
instance and the local demo; a multi-replica deployment needs a shared store (see docs/security.md).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import defaultdict, deque

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nimbrix_api.core.business_metrics import RATE_LIMITED
from nimbrix_api.core.request_context import get_request_id

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
_DOCS_PREFIXES = ("/api/v1/docs", "/api/v1/openapi.json", "/docs/oauth2-redirect")
_EXEMPT_FROM_LIMITS = ("/api/v1/health/", "/api/v1/metrics")


def _envelope(
    status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> tuple[Message, Message]:
    body = json.dumps(
        {"error": {"code": code, "message": message, "request_id": get_request_id(), "details": []}}
    ).encode()
    raw_headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    raw_headers += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return (
        {"type": "http.response.start", "status": status, "headers": raw_headers},
        {"type": "http.response.body", "body": body},
    )


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "no-referrer")
                headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
                headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
                if not path.startswith(_DOCS_PREFIXES):
                    headers.setdefault("Content-Security-Policy", API_CSP)
                    headers.setdefault("Cache-Control", "no-store")
                if self.hsts:
                    headers.setdefault(
                        "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
                    )
            await send(message)

        await self.app(scope, receive, with_headers)


class BodySizeLimitMiddleware:
    """Rejects bodies over ``max_bytes`` (declared or streamed) with 413 before handlers run."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length" and value.isdigit() and int(value) > self.max_bytes:
                for message in _envelope(
                    413,
                    "request_too_large",
                    f"Request bodies are limited to {self.max_bytes:,} bytes",
                ):
                    await send(message)
                return
        received = 0
        rejected = False

        async def counting_receive() -> Message:
            nonlocal received, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Answer now, then make the app see a disconnect so it stops reading.
                    rejected = True
                    for reply in _envelope(
                        413,
                        "request_too_large",
                        f"Request bodies are limited to {self.max_bytes:,} bytes",
                    ):
                        await send(reply)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if not rejected:  # the app's own response is dropped once 413 was sent
                await send(message)

        await self.app(scope, counting_receive, guarded_send)


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> float | None:
        """Record a hit; return seconds to wait if the key is over its limit, else None."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(0.0, hits[0] + self.window - now)
            hits.append(now)
            if len(self._hits) > 50_000:  # bound memory under key-spraying
                self._hits = defaultdict(deque, {k: v for k, v in self._hits.items() if v})
            return None


def _client_key(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name == b"authorization" and value.lower().startswith(b"bearer "):
            return "s:" + hashlib.sha256(value[7:]).hexdigest()[:32]
        if name == b"cookie":
            for part in value.decode("latin-1").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "nimbrix_session" and v:
                    return "s:" + hashlib.sha256(v.encode()).hexdigest()[:32]
    client = scope.get("client")
    return "ip:" + (client[0] if client else "unknown")


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, *, requests_per_minute: int, login_per_minute: int) -> None:
        self.app = app
        self.general = SlidingWindowLimiter(requests_per_minute)
        self.login = SlidingWindowLimiter(login_per_minute)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path", "").startswith(_EXEMPT_FROM_LIMITS):
            await self.app(scope, receive, send)
            return
        path, method = scope.get("path", ""), scope.get("method", "")
        if path == "/api/v1/auth/login" and method == "POST":
            client = scope.get("client")
            bucket, retry = "login", self.login.hit("ip:" + (client[0] if client else "unknown"))
        else:
            bucket, retry = "general", self.general.hit(_client_key(scope))
        if retry is not None:
            RATE_LIMITED.labels(bucket=bucket).inc()
            for message in _envelope(
                429,
                "rate_limited",
                "Too many requests; slow down",
                {"Retry-After": str(int(retry) + 1)},
            ):
                await send(message)
            return
        await self.app(scope, receive, send)
