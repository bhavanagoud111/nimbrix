from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine

from nimbrix_api.core.config import Settings
from nimbrix_api.db.session import Database
from nimbrix_api.main import create_app

UNUSED_DB_URL = "postgresql+psycopg://nimbrix:not-a-real-secret@db.invalid:5432/nimbrix"


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep host NIMBRIX_* variables from leaking into unit tests."""
    for key in list(os.environ):
        if key.startswith("NIMBRIX_") and key != "NIMBRIX_TEST_DATABASE_URL":
            monkeypatch.delenv(key)


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {"database_url": SecretStr(UNUSED_DB_URL), "environment": "test"}
        values.update(overrides)
        return Settings(_env_file=None, **values)

    return factory


@pytest.fixture
def app(make_settings: Callable[..., Settings]) -> FastAPI:
    # The engine is lazy; unit tests override the probe so nothing connects.
    return create_app(make_settings(), Database(create_engine(UNUSED_DB_URL)))


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
