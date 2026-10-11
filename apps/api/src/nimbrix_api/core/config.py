"""Typed, validated runtime configuration.

All settings come from environment variables prefixed with ``NIMBRIX_`` (or a
local ``.env`` file during development). Invalid configuration fails fast at
startup with a message that never echoes the offending values, because some of
them (database URLs) carry credentials.
"""

from __future__ import annotations

from decimal import Decimal
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


class LlmProvider(StrEnum):
    NONE = "none"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"


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

    # --- Authentication (local demo auth; OIDC is the documented production path) ---
    demo_auth_enabled: bool = True
    allow_demo_auth_in_production: bool = False
    session_ttl_hours: int = Field(default=12, ge=1, le=168)
    cookie_secure: bool | None = Field(
        default=None, description="Secure cookie flag; defaults to true in production"
    )
    demo_password: SecretStr | None = Field(
        default=None, description="Password assigned to seeded demo users (seeding only)"
    )
    demo_seed: bool = Field(default=False, description="Opt-in: seed demo tenants and data")

    # --- Control plane -----------------------------------------------------------
    require_distinct_approver: bool = True
    approval_ttl_hours: int = Field(default=24, ge=1, le=720)
    inventory_max_age_hours: int = Field(default=48, ge=1, le=24 * 30)

    # --- Ingestion ---------------------------------------------------------------
    upload_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    max_request_bytes: int = Field(default=12 * 1024 * 1024, ge=1024, le=200 * 1024 * 1024)
    aws_enabled: bool = Field(default=False, description="Allow read-only AWS API calls")
    aws_default_region: str = "us-east-1"

    raw_upload_retention_days: int = Field(default=30, ge=1, le=3650)
    session_retention_days: int = Field(default=7, ge=1, le=365)

    # --- Jobs ----------------------------------------------------------------------
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    job_lease_seconds: int = Field(default=120, ge=10, le=3600)
    worker_metrics_port: int = Field(default=9101, ge=1024, le=65535)

    # --- Investigation agents ---------------------------------------------------------
    llm_provider: LlmProvider = LlmProvider.NONE
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    llm_max_output_tokens: int = Field(default=1500, ge=100, le=16_000)
    llm_input_usd_per_million_tokens: Decimal | None = None
    llm_output_usd_per_million_tokens: Decimal | None = None
    agent_max_steps: int = Field(default=12, ge=3, le=50)
    agent_timeout_seconds: float = Field(default=90.0, gt=0, le=900)
    agent_max_llm_calls: int = Field(default=4, ge=0, le=20)

    # --- Abuse protection -------------------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_login_per_minute: int = Field(default=10, ge=1)
    rate_limit_requests_per_minute: int = Field(default=600, ge=1)

    # --- Telemetry -----------------------------------------------------------------------
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "nimbrix-api"

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
        if (
            self.environment is Environment.PRODUCTION
            and self.demo_auth_enabled
            and not self.allow_demo_auth_in_production
        ):
            raise ValueError(
                "demo authentication is not production-ready; configure OIDC or set "
                "NIMBRIX_ALLOW_DEMO_AUTH_IN_PRODUCTION=true to acknowledge the risk"
            )
        if self.llm_provider is not LlmProvider.NONE and not self.llm_model:
            raise ValueError("llm_model is required when llm_provider is set")
        if (
            self.llm_provider in (LlmProvider.OPENAI, LlmProvider.ANTHROPIC)
            and not self.llm_api_key
        ):
            raise ValueError("llm_api_key is required for the configured llm_provider")
        if self.otel_enabled and not self.otel_exporter_otlp_endpoint:
            raise ValueError("otel_exporter_otlp_endpoint is required when otel_enabled is true")
        return self

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.environment is Environment.PRODUCTION

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
