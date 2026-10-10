from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from nimbrix_api import __version__
from nimbrix_api.api.v1.health import get_database_probe
from nimbrix_api.db.session import DatabaseStatus, expected_migration_head


def _status(current: str | None) -> DatabaseStatus:
    return DatabaseStatus(
        reachable=True,
        latency_ms=1.5,
        current_revision=current,
        expected_revision=expected_migration_head(),
    )


def test_live(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "nimbrix-api", "version": __version__}


def test_ready_when_database_is_at_head(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[get_database_probe] = lambda: (
        lambda: _status(expected_migration_head())
    )
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["environment"] == "test"
    assert body["remediation_mode"] == "simulation_only"
    assert body["checks"]["database"] == {"status": "ok", "latency_ms": 1.5, "detail": None}
    assert body["checks"]["migrations"]["current_revision"] == expected_migration_head()


def test_not_ready_when_migrations_are_pending(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[get_database_probe] = lambda: lambda: _status(None)
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["migrations"] == {
        "status": "fail",
        "current_revision": None,
        "expected_revision": expected_migration_head(),
    }


def test_not_ready_when_database_unreachable_does_not_leak_details(
    app: FastAPI, client: TestClient
) -> None:
    def failing_probe() -> DatabaseStatus:
        raise OperationalError("connect", {}, Exception("password=hunter2 host=10.0.0.5"))

    app.dependency_overrides[get_database_probe] = lambda: failing_probe
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == {
        "status": "fail",
        "latency_ms": None,
        "detail": "database unreachable",
    }
    assert response.json()["checks"]["migrations"]["expected_revision"] == expected_migration_head()
    assert "hunter2" not in response.text
    assert "10.0.0.5" not in response.text


def test_migration_chain_has_a_single_head() -> None:
    head = expected_migration_head()
    assert head is not None
    assert head.startswith("00")
