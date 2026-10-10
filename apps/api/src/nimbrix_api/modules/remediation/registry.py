"""Action registry and policy evaluation (ADR-0005).

Exactly one action exists: ``simulate_stop_nonprod_instance``. Policy evaluation is pure
read-only logic: it never changes anything and is reused by the approval flow, the
execution-time revalidation, and the investigation agents' policy-review node.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from nimbrix_api.models import ResourceInventory
from nimbrix_api.modules.ingestion.records import NONPROD_ENVIRONMENTS
from nimbrix_api.modules.pricing import HOURS_PER_MONTH, PRICE_SOURCE
from nimbrix_api.modules.recommendations.rules import PROTECTED_TAG

SIMULATE_STOP = "simulate_stop_nonprod_instance"


class StopInstancePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str = Field(min_length=3, max_length=512)
    account_id: str = Field(pattern=r"^\d{12}$")
    region: str = Field(pattern=r"^[a-z]{2}(-gov)?-[a-z]+-\d$")
    reason: str = Field(min_length=3, max_length=1000)
    recommendation_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str
    mode: str  # "simulation" only; live actions do not exist
    payload_model: type[StopInstancePayload]
    resource_type: str


REGISTRY: dict[str, ActionSpec] = {
    SIMULATE_STOP: ActionSpec(
        name=SIMULATE_STOP,
        description="Simulate stopping one running non-production EC2 instance. No cloud call is made.",
        mode="simulation",
        payload_model=StopInstancePayload,
        resource_type="ec2_instance",
    )
}


@dataclass
class PolicyResult:
    action_type: str
    allowed: bool
    checks: list[dict[str, Any]]
    payload: dict[str, Any] | None
    resource: dict[str, Any] | None = None
    impact_preview: dict[str, Any] = field(default_factory=dict)
    rollback_plan: dict[str, Any] = field(default_factory=dict)

    @property
    def denial_reasons(self) -> list[str]:
        return [c["detail"] for c in self.checks if not c["passed"]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "allowed": self.allowed,
            "checks": self.checks,
            "denial_reasons": self.denial_reasons,
            "evaluated_at": datetime.now(UTC).isoformat(),
        }


def canonical_payload_hash(tenant_id: uuid.UUID, action_type: str, payload: dict[str, Any]) -> str:
    """SHA-256 over canonical JSON; any change to tenant, action, or payload changes it."""
    canonical = json.dumps(
        {"tenant_id": str(tenant_id), "action_type": action_type, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "passed": passed, "detail": detail}


def evaluate_policy(
    session: Session,
    tenant_id: uuid.UUID,
    action_type: str,
    payload: dict[str, Any],
    *,
    inventory_max_age_hours: int,
    now: datetime | None = None,
) -> PolicyResult:
    now = now or datetime.now(UTC)
    spec = REGISTRY.get(action_type)
    if spec is None:
        return PolicyResult(
            action_type,
            False,
            [_check("action_registered", False, f"'{action_type}' is not an allowlisted action")],
            None,
        )
    checks = [
        _check("action_registered", True, f"'{action_type}' is allowlisted ({spec.mode} only)")
    ]
    try:
        parsed = spec.payload_model.model_validate(payload)
    except ValidationError as exc:
        fields = ", ".join(
            ".".join(str(p) for p in e["loc"]) for e in exc.errors(include_input=False)
        )
        checks.append(_check("payload_schema", False, f"invalid payload fields: {fields}"))
        return PolicyResult(action_type, False, checks, None)
    checks.append(_check("payload_schema", True, "payload matches the action schema"))
    normalized = parsed.model_dump(mode="json")

    resource = session.scalar(
        select(ResourceInventory).where(
            ResourceInventory.tenant_id == tenant_id,
            ResourceInventory.resource_id == parsed.resource_id,
        )
    )
    if resource is None:
        checks.append(
            _check("resource_in_tenant", False, "resource not found in this tenant's inventory")
        )
        return PolicyResult(action_type, False, checks, normalized)
    checks.append(_check("resource_in_tenant", True, "resource belongs to this tenant"))
    matches = resource.account_id == parsed.account_id and resource.region == parsed.region
    checks.append(
        _check(
            "account_region_match",
            matches,
            "payload account/region match inventory"
            if matches
            else "payload account/region differ from inventory",
        )
    )
    checks.append(
        _check(
            "resource_type",
            resource.resource_type == spec.resource_type,
            f"type is {resource.resource_type}",
        )
    )
    nonprod = resource.environment in NONPROD_ENVIRONMENTS
    checks.append(
        _check(
            "environment_nonprod",
            nonprod,
            f"environment tag is {resource.environment or 'missing'}",
        )
    )
    protected = str(resource.tags.get(PROTECTED_TAG, "")).lower() == "true"
    checks.append(
        _check(
            "not_protected", not protected, f"{PROTECTED_TAG}={'true' if protected else 'unset'}"
        )
    )
    checks.append(
        _check("state_running", resource.state == "running", f"state is {resource.state}")
    )
    age = now - resource.observed_at
    fresh = age <= timedelta(hours=inventory_max_age_hours)
    checks.append(
        _check(
            "inventory_fresh",
            fresh,
            f"observed {age.total_seconds() / 3600:.1f} h ago (limit {inventory_max_age_hours} h)",
        )
    )
    checks.append(
        _check("simulation_only", spec.mode == "simulation", "no live cloud write is possible")
    )

    hourly = resource.hourly_cost
    monthly = (
        (hourly * HOURS_PER_MONTH).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if hourly is not None
        else None
    )
    snapshot = {
        "resource_id": resource.resource_id,
        "name": resource.name,
        "resource_type": resource.resource_type,
        "account_id": resource.account_id,
        "region": resource.region,
        "environment": resource.environment,
        "team": resource.team,
        "state": resource.state,
        "instance_type": resource.attributes.get("instance_type"),
        "observed_at": resource.observed_at.isoformat(),
        "inventory_id": str(resource.id),
    }
    impact = {
        "mode": "simulation",
        "blast_radius": "one instance; attached EBS volumes and IPs are untouched",
        "current_monthly_compute_cost": str(monthly) if monthly is not None else None,
        "estimated_monthly_savings": str(monthly) if monthly is not None else None,
        "savings_basis": f"hourly list price x 730 h ({PRICE_SOURCE})"
        if monthly is not None
        else "price unknown",
        "excluded_costs": ["attached EBS storage keeps billing", "data transfer"],
        "realized": False,
    }
    rollback = {
        "action": "simulate_start_instance",
        "steps": [f"Start instance {resource.resource_id} in {resource.region} (simulated)"],
        "data_loss": "none for EBS-backed instances; instance-store data would be lost on a real stop",
        "estimated_recovery_minutes": 5,
    }
    allowed = all(c["passed"] for c in checks)
    return PolicyResult(action_type, allowed, checks, normalized, snapshot, impact, rollback)
