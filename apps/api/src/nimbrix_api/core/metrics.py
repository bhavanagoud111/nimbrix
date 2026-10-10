"""Prometheus HTTP metrics. Labels use route templates to bound cardinality."""

from __future__ import annotations

import time

from prometheus_client import CollectorRegistry, Counter, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "nimbrix_http_requests_total",
    "HTTP requests handled by the API",
    labelnames=("method", "route", "status"),
    registry=REGISTRY,
)
HTTP_LATENCY = Histogram(
    "nimbrix_http_request_duration_seconds",
    "HTTP request latency in seconds",
    labelnames=("method", "route"),
    registry=REGISTRY,
)

UNMATCHED_ROUTE = "__unmatched__"


def _route_template(scope: Scope) -> str:
    """The router records the matched route on the scope once routing completes."""
    route = scope.get("route")
    path = getattr(route, "path_format", None) or getattr(route, "path", None)
    return str(path) if path else UNMATCHED_ROUTE


class MetricsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        status = 500
        start = time.perf_counter()

        async def capture_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, capture_status)
        finally:
            route = _route_template(scope)
            method = scope["method"]
            HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
            HTTP_LATENCY.labels(method=method, route=route).observe(time.perf_counter() - start)
