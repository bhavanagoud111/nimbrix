"""API contract rules enforced over the generated OpenAPI document and route table."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute
from sqlalchemy import create_engine

from nimbrix_api.core.config import Settings
from nimbrix_api.core.deps import get_auth_context, idempotency_key
from nimbrix_api.db.session import Database
from nimbrix_api.main import create_app
from nimbrix_api.openapi_export import build_openapi
from tests.conftest import UNUSED_DB_URL

CONTRACT = Path(__file__).resolve().parents[4] / "packages" / "contracts" / "openapi.json"
PUBLIC = {
    ("GET", "/api/v1/health/live"),
    ("GET", "/api/v1/health/ready"),
    ("GET", "/api/v1/metrics"),
    ("POST", "/api/v1/auth/login"),
}
# Mutations that are naturally idempotent or session-bound and therefore exempt.
NO_IDEMPOTENCY_KEY = {
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
    "/api/v1/approvals/preview",
    "/api/v1/recommendations/{rec_id}/simulate",
    "/api/v1/connections/aws/validate",
    "/api/v1/approvals/{approval_id}/approve",
    "/api/v1/approvals/{approval_id}/reject",
    "/api/v1/recommendations/{rec_id}/dismiss",
    "/api/v1/anomalies/{anomaly_id}/status",
    "/api/v1/investigations/{investigation_id}/cancel",
    "/api/v1/budgets/alerts/{alert_id}/acknowledge",
    "/api/v1/analysis/run",
    "/api/v1/forecasts/run",
}


def _routes(make_settings: Any) -> list[APIRoute]:
    app = create_app(make_settings(), Database(create_engine(UNUSED_DB_URL)))
    return [r for r in app.routes if isinstance(r, APIRoute)]


def _deps(route: APIRoute) -> set[Any]:
    calls, stack = set(), list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        calls.add(dep.call)
        stack.extend(dep.dependencies)
    return calls


def test_committed_contract_matches_code() -> None:
    generated = json.dumps(build_openapi(), indent=2, sort_keys=True) + "\n"
    assert CONTRACT.read_text() == generated, "run `make contracts` and commit the result"


def test_every_operation_documents_the_error_envelope() -> None:
    paths: dict[str, dict[str, Any]] = build_openapi()["paths"]  # type: ignore[assignment]
    for path, operations in paths.items():
        for method, op in operations.items():
            assert {"4XX", "5XX"} <= set(op["responses"]), f"{method} {path}"


def test_every_non_public_route_requires_authentication(
    make_settings: Callable[..., Settings],
) -> None:
    for route in _routes(make_settings):
        for method in route.methods or set():
            if (method, route.path) in PUBLIC:
                continue
            assert get_auth_context in _deps(route), f"{method} {route.path} has no auth dependency"


def test_mutations_require_idempotency_keys(make_settings: Callable[..., Settings]) -> None:
    for route in _routes(make_settings):
        if (route.methods or set()) & {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        } and route.path not in NO_IDEMPOTENCY_KEY:
            assert idempotency_key in _deps(route), f"{route.path} lacks Idempotency-Key"
