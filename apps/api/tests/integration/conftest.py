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
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, make_url, select, text
from sqlalchemy.orm import Session, sessionmaker

from nimbrix_api.core.config import Settings
from nimbrix_api.core.security import hash_password
from nimbrix_api.db.session import MIGRATIONS_LOCATION, Database, build_engine
from nimbrix_api.main import create_app
from nimbrix_api.models import Membership, Role, Tenant, User
from nimbrix_api.modules.ingestion.requests import request_demo_import
from nimbrix_api.modules.jobs.runner import drain
from nimbrix_api.seed import seed_identities

ADMIN_URL = os.environ.get("NIMBRIX_TEST_DATABASE_URL")
DEMO_PASSWORD = "integration-test-password"
DEMO_END = date(2026, 9, 30)


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


@dataclass(frozen=True)
class TenantUsers:
    tenant_id: uuid.UUID
    slug: str
    emails: dict[str, str]  # role -> email


def create_tenant(
    session: Session, roles: tuple[str, ...] = ("viewer", "analyst", "approver", "admin")
) -> TenantUsers:
    """A brand-new tenant with one user per role (password DEMO_PASSWORD)."""
    slug = f"t-{uuid.uuid4().hex[:10]}"
    tenant = Tenant(slug=slug, name=f"Test {slug}")
    session.add(tenant)
    session.flush()
    role_ids = {r.name: r.id for r in session.scalars(select(Role))}
    emails: dict[str, str] = {}
    password_hash = hash_password(DEMO_PASSWORD)
    for role in roles:
        email = f"{role}@{slug}.test"
        user = User(
            email=email, display_name=f"{role} {slug}", password_hash=password_hash, is_active=True
        )
        session.add(user)
        session.flush()
        session.add(Membership(tenant_id=tenant.id, user_id=user.id, role_id=role_ids[role]))
        emails[role] = email
    session.commit()
    return TenantUsers(tenant_id=tenant.id, slug=slug, emails=emails)


def load_demo(
    factory: sessionmaker[Session],
    settings: Settings,
    tenant_id: uuid.UUID,
    end_date: date,
    days: int = 120,
) -> None:
    with factory() as session:
        request_demo_import(
            session, tenant_id=tenant_id, user_id=None, end_date=end_date, days=days, seed=42
        )
        session.commit()
    drain(factory, settings)


@pytest.fixture(scope="module")
def demo_tenant(app_database_url: str) -> Iterator[TenantUsers]:
    """A tenant loaded with the 120-day demo dataset ending 2026-09-30 and analyzed once."""
    engine = create_engine(app_database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(
        database_url=SecretStr(app_database_url), environment="test", _env_file=None
    )  # type: ignore[arg-type]
    with factory() as session:
        users = create_tenant(session, ("viewer", "analyst", "approver", "auditor", "admin"))
    load_demo(factory, settings, users.tenant_id, DEMO_END)
    yield users
    engine.dispose()


_DRAIN: dict[str, Any] = {}


@pytest.fixture(autouse=True)
def _drain_registry(app_engine: Engine, app_settings: Settings) -> None:
    _DRAIN["factory"] = sessionmaker(bind=app_engine, expire_on_commit=False)
    _DRAIN["settings"] = app_settings


def drain_jobs() -> int:
    """Run queued jobs inline (the test equivalent of the worker)."""
    return drain(_DRAIN["factory"], _DRAIN["settings"])
