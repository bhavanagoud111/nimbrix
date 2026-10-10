"""Integration fixtures: each test session gets a brand-new, throwaway database.

Set ``NIMBRIX_TEST_DATABASE_URL`` to a role that may CREATE DATABASE (the local
Docker Compose and CI Postgres users can). Tests are skipped, and reported as
skipped, when it is absent.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from alembic.config import Config
from sqlalchemy import Engine, create_engine, make_url, text

from nimbrix_api.db.session import MIGRATIONS_LOCATION

ADMIN_URL = os.environ.get("NIMBRIX_TEST_DATABASE_URL")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/integration" in str(item.path):
            item.add_marker(pytest.mark.integration)
            if not ADMIN_URL:
                item.add_marker(pytest.mark.skip(reason="NIMBRIX_TEST_DATABASE_URL is not set"))


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
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


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    engine = create_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", MIGRATIONS_LOCATION)
    config.attributes["database_url"] = database_url
    return config
