"""Request IDs, error envelope, metrics, and OpenAPI generation."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nimbrix_api.core.errors import ApiError
from nimbrix_api.core.request_context import get_request_id
from nimbrix_api.openapi_export import build_openapi


@pytest.fixture
def probe_app(app: FastAPI) -> FastAPI:
    @app.get("/api/v1/_test/boom")
    def boom() -> None:
        raise RuntimeError("database password=hunter2 exploded")

    @app.get("/api/v1/_test/conflict")
    def conflict() -> None:
        raise ApiError(409, "duplicate_import", "Import already exists")

    @app.get("/api/v1/_test/items/{item_id}")
    def item(item_id: int) -> dict[str, int | str | None]:
        return {"item_id": item_id, "request_id": get_request_id()}

    return app


def test_request_id_is_generated(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")
    request_id = response.headers["X-Request-ID"]
    assert len(request_id) == 32


def test_safe_request_id_is_propagated(probe_app: FastAPI, client: TestClient) -> None:
    response = client.get("/api/v1/_test/items/7", headers={"X-Request-ID": "trace-abc.123"})
    assert response.headers["X-Request-ID"] == "trace-abc.123"
    assert response.json() == {"item_id": 7, "request_id": "trace-abc.123"}


@pytest.mark.parametrize("unsafe", ["short", "has spaces in it", "x" * 65, "bad<script>id"])
def test_unsafe_request_id_is_replaced(client: TestClient, unsafe: str) -> None:
    response = client.get("/api/v1/health/live", headers={"X-Request-ID": unsafe})
    assert response.headers["X-Request-ID"] != unsafe


def test_not_found_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist", headers={"X-Request-ID": "req-00000001"})
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Not Found",
            "request_id": "req-00000001",
            "details": [],
        }
    }


def test_method_not_allowed_uses_error_envelope(client: TestClient) -> None:
    response = client.post("/api/v1/health/live")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_validation_error_hides_submitted_values(probe_app: FastAPI, client: TestClient) -> None:
    response = client.get("/api/v1/_test/items/not-a-number-secret")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["field"] == "path.item_id"
    assert "not-a-number-secret" not in response.text


def test_api_error_is_typed(probe_app: FastAPI, client: TestClient) -> None:
    response = client.get("/api/v1/_test/conflict")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_import"


def test_unhandled_error_is_generic(probe_app: FastAPI, client: TestClient) -> None:
    response = client.get("/api/v1/_test/boom")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "hunter2" not in response.text
    assert response.headers["X-Request-ID"]


def test_metrics_use_route_templates(probe_app: FastAPI, client: TestClient) -> None:
    client.get("/api/v1/_test/items/1")
    client.get("/api/v1/_test/items/2")
    client.get("/api/v1/nope/12345")
    client.get("/api/v1/health/live")
    body = client.get("/api/v1/metrics").text
    assert 'route="/api/v1/health/live",status="200"' in body
    assert 'route="/api/v1/_test/items/{item_id}"' in body
    assert 'route="__unmatched__"' in body
    assert "/api/v1/_test/items/1" not in body
    assert "/api/v1/nope/12345" not in body


def test_openapi_document_exposes_phase0_paths() -> None:
    document = build_openapi()
    paths = document["paths"]
    assert isinstance(paths, dict)
    assert set(paths) == {"/api/v1/health/live", "/api/v1/health/ready", "/api/v1/metrics"}
