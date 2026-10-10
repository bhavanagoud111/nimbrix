"""Typed, validated runtime configuration.

All settings come from environment variables prefixed with ``NIMBRIX_`` (or a
local ``.env`` file during development). Invalid configuration fails fast at
startup with a message that never echoes the offending values, because some of
them (database URLs) carry credentials.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Self

from pydantic import (
    Field,
    PostgresDsn,
    SecretStr,
    TypeAdapter,
    ValidationError,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    CI = "ci"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class RemediationMode(StrEnum):
    """How remediation actions may run. Only simulation exists today."""

    SIMULATION_ONLY = "simulation_only"


_POSTGRES_DSN: TypeAdapter[PostgresDsn] = TypeAdapter(PostgresDsn)


class ConfigurationError(RuntimeError):
    """Raised when settings are invalid. The message is safe to log."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NIMBRIX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: Environment = Environment.LOCAL
    log_level: LogLevel = LogLevel.INFO
    database_url: SecretStr = Field(
        description="SQLAlchemy URL using the psycopg (v3) driver, e.g. "
        "postgresql+psycopg://user:password@host:5432/nimbrix",
    )
    database_connect_timeout_seconds: int = Field(default=3, ge=1, le=30)
    database_statement_timeout_ms: int = Field(default=5_000, ge=100, le=600_000)
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    live_actions_enabled: bool = Field(
        default=False,
        description="Must stay false: no live cloud remediation adapter exists yet.",
    )

    @model_validator(mode="before")
    @classmethod
    def _split_origins(cls, data: object) -> object:
        if isinstance(data, dict):
            raw = data.get("cors_allowed_origins")
            if isinstance(raw, str):
                data = {
                    **data,
                    "cors_allowed_origins": [o.strip() for o in raw.split(",") if o.strip()],
                }
        return data

    @model_validator(mode="after")
    def _validate(self) -> Self:
        url = self.database_url.get_secret_value()
        try:
            _POSTGRES_DSN.validate_python(url)
        except ValidationError:
            raise ValueError("database_url is not a valid PostgreSQL URL") from None
        if not url.startswith("postgresql+psycopg://"):
            raise ValueError("database_url must use the 'postgresql+psycopg://' driver prefix")
        if "*" in self.cors_allowed_origins:
            raise ValueError("cors_allowed_origins must be an explicit allowlist; '*' is rejected")
        for origin in self.cors_allowed_origins:
            if not origin.startswith(("http://", "https://")):
                raise ValueError(
                    "each cors_allowed_origins entry must start with http:// or https://"
                )
        if self.live_actions_enabled:
            raise ValueError(
                "live_actions_enabled=true is not supported: only simulated remediation exists"
            )
        if self.environment is Environment.PRODUCTION and self.log_level is LogLevel.DEBUG:
            raise ValueError("DEBUG logging is not allowed in production")
        return self

    @property
    def remediation_mode(self) -> RemediationMode:
        return RemediationMode.SIMULATION_ONLY


def describe_validation_error(exc: ValidationError) -> str:
    """Summarize a settings ValidationError without including input values."""
    problems = []
    for err in exc.errors(include_input=False, include_url=False):
        location = ".".join(str(part) for part in err["loc"]) or "settings"
        field = f"NIMBRIX_{location.upper()}" if err["loc"] else location
        problems.append(f"{field}: {err['msg']}")
    return "Invalid Nimbrix configuration -> " + "; ".join(problems)


def load_settings() -> Settings:
    try:
        return Settings()  # values come from the environment
    except ValidationError as exc:
        raise ConfigurationError(describe_validation_error(exc)) from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
