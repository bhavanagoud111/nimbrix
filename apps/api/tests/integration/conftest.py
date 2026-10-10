"""Integration fixtures backed by real PostgreSQL.

Set ``NIMBRIX_TEST_DATABASE_URL`` to a role that may CREATE DATABASE (the local
Docker Compose and CI Postgres users can). Each test session works in brand-new,
throwaway databases. Tests are skipped, and reported as skipped, when it is absent.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.orm import Session, sessionmaker

from nimbrix_api.core.config import Settings
from nimbrix_api.db.session import MIGRATIONS_LOCATION, Database, build_engine
from nimbrix_api.main import create_app
from nimbrix_api.seed import seed_identities

ADMIN_URL = os.environ.get("NIMBRIX_TEST_DATABASE_URL")
DEMO_PASSWORD = "integration-test-password"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/integration" in str(item.path):
            item.add_marker(pytest.mark.integration)
            if not ADMIN_URL:
                item.add_marker(pytest.mark.skip(reason="NIMBRIX_TEST_DATABASE_URL is not set"))


@contextmanager
def temporary_database() -> Iterator[str]:
    assert ADMIN_URL
    admin_engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    name = f"nimbrix_it_{uuid.uuid4().hex[:12]}"
    with admin_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    finally:
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin_engine.dispose()


def alembic_config_for(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", MIGRATIONS_LOCATION)
    config.attributes["database_url"] = url
    return config


@pytest.fixture
def fresh_database_url() -> Iterator[str]:
    """An empty database for migration lifecycle tests."""
    with temporary_database() as url:
        yield url


@pytest.fixture
def alembic_config(fresh_database_url: str) -> Config:
    return alembic_config_for(fresh_database_url)


@pytest.fixture
def engine(fresh_database_url: str) -> Iterator[Engine]:
    engine = create_engine(fresh_database_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_database_url() -> Iterator[str]:
    """Migrated + seeded database shared by API integration tests."""
    with temporary_database() as url:
        command.upgrade(alembic_config_for(url), "head")
        engine = create_engine(url)
        with Session(engine) as session:
            seed_identities(session, DEMO_PASSWORD)
            session.commit()
        engine.dispose()
        yield url


@pytest.fixture(scope="session")
def app_engine(app_database_url: str) -> Iterator[Engine]:
    engine = create_engine(app_database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db(app_engine: Engine) -> Iterator[Session]:
    with sessionmaker(bind=app_engine, expire_on_commit=False)() as session:
        yield session


@pytest.fixture
def app_settings(app_database_url: str, make_settings: Callable[..., Settings]) -> Settings:
    return make_settings(database_url=SecretStr(app_database_url), rate_limit_enabled=False)


@pytest.fixture
def client_for(app_settings: Settings) -> Iterator[Callable[..., TestClient]]:
    """Factory: an authenticated TestClient (own cookie jar) for a seeded demo user."""
    clients: list[TestClient] = []
    app = create_app(app_settings, Database(build_engine(app_settings)))

    def factory(email: str | None = None, **settings_overrides: Any) -> TestClient:
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        clients.append(client)
        if email:
            response = client.post(
                "/api/v1/auth/login", json={"email": email, "password": DEMO_PASSWORD}
            )
            assert response.status_code == 200, response.text
            client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


def idem() -> dict[str, str]:
    return {"Idempotency-Key": f"test-{uuid.uuid4().hex}"}
