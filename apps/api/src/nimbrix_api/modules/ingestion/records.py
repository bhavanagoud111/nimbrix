"""Normalized, validated record shapes that every source adapter produces."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

ENVIRONMENT_TAG_KEYS = ("environment", "env", "stage")
TEAM_TAG_KEYS = ("team", "owner-team", "owner_team", "cost-center-team")
_ENV_ALIASES = {
    "prod": "prod",
    "production": "prod",
    "prd": "prod",
    "staging": "staging",
    "stage": "staging",
    "stg": "staging",
    "dev": "dev",
    "development": "dev",
    "test": "test",
    "qa": "test",
    "sandbox": "sandbox",
}
NONPROD_ENVIRONMENTS = frozenset({"staging", "dev", "test", "sandbox"})


def _tag(tags: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    lowered = {str(k).lower(): v for k, v in tags.items()}
    for key in keys:
        value = lowered.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def derive_environment(tags: dict[str, Any]) -> str | None:
    raw = _tag(tags, ENVIRONMENT_TAG_KEYS)
    if raw is None:
        return None
    return _ENV_ALIASES.get(raw.lower(), raw.lower()[:64])


def derive_team(tags: dict[str, Any]) -> str | None:
    raw = _tag(tags, TEAM_TAG_KEYS)
    return raw.lower()[:128] if raw else None


@dataclass(frozen=True)
class CostRecord:
    source_record_id: str
    provider: str
    account_id: str
    service: str
    usage_start: datetime
    usage_end: datetime
    amount: Decimal
    currency: str
    region: str | None = None
    resource_id: str | None = None
    usage_type: str | None = None
    usage_quantity: Decimal | None = None
    usage_unit: str | None = None
    tags: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def usage_date(self) -> date:
        return self.usage_start.date()


@dataclass(frozen=True)
class ResourceRecord:
    provider: str
    account_id: str
    region: str
    resource_id: str
    resource_type: str
    state: str
    observed_at: datetime
    name: str | None = None
    tags: dict[str, Any] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)
    hourly_cost: Decimal | None = None
    monthly_cost: Decimal | None = None
    currency: str = "USD"
    utilization: dict[str, Any] | None = None


@dataclass(frozen=True)
class RowError:
    row: int | None
    code: str
    message: str
    field: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"row": self.row, "code": self.code, "message": self.message, "field": self.field}
