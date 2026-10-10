"""Runs against real PostgreSQL: migrations apply/rollback and readiness reflects them."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from nimbrix_api import models  # noqa: F401 - registers tables on Base.metadata
from nimbrix_api.core.config import Settings
from nimbrix_api.db.base import Base
from nimbrix_api.db.session import Database, build_engine, expected_migration_head
from nimbrix_api.main import create_app


def _ready(database_url: str, make_settings: Callable[..., Settings]) -> tuple[int, dict[str, Any]]:
    settings = make_settings(database_url=SecretStr(database_url))
    app = create_app(settings, Database(build_engine(settings)))
    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")
    return response.status_code, response.json()


def test_full_migration_lifecycle(
    alembic_config: Config,
    engine: Engine,
    fresh_database_url: str,
    make_settings: Callable[..., Settings],
) -> None:
    command.downgrade(alembic_config, "base")

    # Empty database: reachable, but migrations are not applied.
    status, body = _ready(fresh_database_url, make_settings)
    assert status == 503
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["migrations"]["current_revision"] is None

    command.upgrade(alembic_config, "head")
    inspector = inspect(engine)
    assert "tenants" in inspector.get_table_names()
    assert {c["name"] for c in inspector.get_columns("tenants")} == {
        "id",
        "slug",
        "name",
        "created_at",
        "updated_at",
    }
    assert inspector.get_pk_constraint("tenants")["name"] == "pk_tenants"

    status, body = _ready(fresh_database_url, make_settings)
    assert status == 200
    assert body["status"] == "ready"
    head = expected_migration_head()
    assert body["checks"]["migrations"] == {
        "status": "ok",
        "current_revision": head,
        "expected_revision": head,
    }

    # Upgrading twice is a no-op; downgrade fully reverses the schema.
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    assert "tenants" not in inspect(engine).get_table_names()
    command.upgrade(alembic_config, "head")


def test_tenant_constraints(alembic_config: Config, engine: Engine) -> None:
    command.upgrade(alembic_config, "head")
    insert = text("INSERT INTO tenants (slug, name) VALUES (:slug, :name) RETURNING id, created_at")
    with engine.begin() as conn:
        row = conn.execute(insert, {"slug": "acme-demo", "name": "Acme Demo"}).one()
        assert row.id is not None
        assert row.created_at.tzinfo is not None
    with pytest.raises(IntegrityError, match="uq_tenants_slug"), engine.begin() as conn:
        conn.execute(insert, {"slug": "acme-demo", "name": "Duplicate"})
    with pytest.raises(IntegrityError, match="ck_tenants_slug_format"), engine.begin() as conn:
        conn.execute(insert, {"slug": "Not A Slug!", "name": "Bad"})
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tenants"))


def test_models_match_migrations(alembic_config: Config, engine: Engine) -> None:
    """Autogenerate must find no drift between ORM models and the migrated schema."""
    command.upgrade(alembic_config, "head")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []
