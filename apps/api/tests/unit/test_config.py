from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from nimbrix_api.core.config import (
    ConfigurationError,
    Environment,
    RemediationMode,
    Settings,
    get_settings,
    load_settings,
)

SECRET = "s3cr3t-pa55word"
VALID_URL = f"postgresql+psycopg://nimbrix:{SECRET}@localhost:5432/nimbrix"


def test_defaults_are_safe(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    assert settings.live_actions_enabled is False
    assert settings.remediation_mode is RemediationMode.SIMULATION_ONLY
    assert settings.cors_allowed_origins == []


def test_reads_environment_and_splits_cors_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIMBRIX_DATABASE_URL", VALID_URL)
    monkeypatch.setenv("NIMBRIX_ENVIRONMENT", "ci")
    monkeypatch.setenv("NIMBRIX_CORS_ALLOWED_ORIGINS", "http://localhost:3200, https://app.example")
    settings = load_settings()
    assert settings.environment is Environment.CI
    assert settings.cors_allowed_origins == ["http://localhost:3200", "https://app.example"]


def test_database_url_is_secret(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(database_url=VALID_URL)
    assert SECRET not in repr(settings)
    assert SECRET not in str(settings.model_dump())


def test_missing_database_url_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigurationError, match="NIMBRIX_DATABASE_URL"):
        load_settings()


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"NIMBRIX_DATABASE_URL": f"postgresql://nimbrix:{SECRET}@localhost/db"}, "driver prefix"),
        ({"NIMBRIX_DATABASE_URL": f"mysql+pymysql://u:{SECRET}@localhost/db"}, "PostgreSQL URL"),
        ({"NIMBRIX_CORS_ALLOWED_ORIGINS": "*"}, "explicit allowlist"),
        ({"NIMBRIX_CORS_ALLOWED_ORIGINS": "localhost:3200"}, "http:// or https://"),
        ({"NIMBRIX_LIVE_ACTIONS_ENABLED": "true"}, "only simulated remediation"),
        ({"NIMBRIX_ENVIRONMENT": "production", "NIMBRIX_LOG_LEVEL": "DEBUG"}, "DEBUG logging"),
        ({"NIMBRIX_ENVIRONMENT": "staging"}, "NIMBRIX_ENVIRONMENT"),
        ({"NIMBRIX_DATABASE_CONNECT_TIMEOUT_SECONDS": "0"}, "NIMBRIX_DATABASE_CONNECT_TIMEOUT"),
    ],
)
def test_invalid_configuration_is_rejected_without_leaking_values(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: str
) -> None:
    monkeypatch.setenv("NIMBRIX_DATABASE_URL", VALID_URL)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ConfigurationError) as info:
        load_settings()
    message = str(info.value)
    assert expected in message
    assert SECRET not in message
    assert info.value.__cause__ is None  # the raw ValidationError (with inputs) is suppressed


def test_settings_are_immutable(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    with pytest.raises(ValidationError):
        settings.live_actions_enabled = True  # type: ignore[misc]


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIMBRIX_DATABASE_URL", VALID_URL)
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
