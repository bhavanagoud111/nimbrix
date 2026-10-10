"""Engine/session lifecycle and the database checks used by readiness probes."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from nimbrix_api.core.config import Settings

MIGRATIONS_LOCATION = "nimbrix_api:migrations"


def build_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={
            "connect_timeout": settings.database_connect_timeout_seconds,
            "options": f"-c statement_timeout={settings.database_statement_timeout_ms}",
            "application_name": "nimbrix-api",
        },
    )


class Database:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def session(self) -> Iterator[Session]:
        with self.session_factory() as session:
            yield session

    def dispose(self) -> None:
        self.engine.dispose()


@lru_cache(maxsize=1)
def expected_migration_head() -> str | None:
    config = Config()
    config.set_main_option("script_location", MIGRATIONS_LOCATION)
    return ScriptDirectory.from_config(config).get_current_head()


@dataclass(frozen=True)
class DatabaseStatus:
    reachable: bool
    latency_ms: float | None
    current_revision: str | None
    expected_revision: str | None

    @property
    def migrations_current(self) -> bool:
        return self.reachable and self.current_revision == self.expected_revision


def check_database(engine: Engine) -> DatabaseStatus:
    """Probe connectivity and migration state. Raises on connectivity failure."""
    start = time.perf_counter()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        current = MigrationContext.configure(conn).get_current_revision()
    return DatabaseStatus(
        reachable=True,
        latency_ms=latency_ms,
        current_revision=current,
        expected_revision=expected_migration_head(),
    )
