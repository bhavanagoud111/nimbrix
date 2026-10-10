"""Phase 5 end to end: LangGraph investigations over the demo spike."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from nimbrix_api.core.config import Settings
from nimbrix_api.modules.agents import service
from nimbrix_api.modules.agents.providers import (
    MalformedOutputError,
    ModelResult,
    ProviderUnavailableError,
)
from nimbrix_api.modules.agents.schemas import Evidence, ModelAnalysis
from nimbrix_api.modules.agents.tools import EvidenceTools, ToolAccessDeniedError, read_only_session
from tests.integration.conftest import DRAIN, TenantUsers, create_tenant, drain_jobs, idem

ClientFor = Callable[..., TestClient]
NODES = [
    "triage",
    "collect_evidence",
    "forecast_context",
    "hypothesize",
    "synthesize_recommendations",
    "policy_review",
    "report",
]


class FakeModel:
    provider = "fake-llm"
    model = "fake-model-1"

    def __init__(self, behaviour: Callable[[list[Evidence]], ModelAnalysis | Exception]) -> None:
        self.behaviour = behaviour
        self.calls = 0

    def analyze(self, question: str, evidence: list[Evidence]) -> ModelResult:
        self.calls += 1
        outcome = self.behaviour(evidence)
        if isinstance(outcome, Exception):
            raise outcome
        return ModelResult(
            analysis=outcome,
            provider=self.provider,
            model=self.model,
            input_tokens=1200,
            output_tokens=300,
            latency_ms=12.0,
        )


@pytest.fixture
def use_model(monkeypatch: pytest.MonkeyPatch) -> Callable[[FakeModel], None]:
    def install(model: FakeModel) -> None:
        monkeypatch.setattr(service, "model_factory", lambda _settings: model)

    return install


def _anomaly_id(client: TestClient, scope_type: str) -> str:
    return str(client.get(f"/api/v1/anomalies?scope_type={scope_type}").json()["items"][0]["id"])


def _investigate(client: TestClient, anomaly_id: str) -> dict[str, Any]:
    response = client.post(f"/api/v1/anomalies/{anomaly_id}/investigate", headers=idem())
    assert response.status_code == 202, response.text
    drain_jobs()
    result: dict[str, Any] = client.get(f"/api/v1/investigations/{response.json()['id']}").json()
    return result


def test_deterministic_investigation_explains_the_spike(
    client_for: ClientFor, demo_tenant: TenantUsers
) -> None:
    analyst = client_for(demo_tenant.emails["analyst"])
    inv = _investigate(analyst, _anomaly_id(analyst, "service"))
    assert inv["state"] == "completed"
    assert inv["mode"] == "deterministic"
    assert inv["model"] is None
    assert [s["node"] for s in inv["steps"]] == NODES
    assert {s["agent_role"] for s in inv["steps"]} == {
        "Cost Analyst Agent",
        "Infrastructure Investigator",
        "Forecast Analyst",
        "Optimization Planner",
        "Report Agent",
    }
    report = inv["report"]
    root = report["root_cause"]
    assert root["confidence"] == "high"
    assert "8 new c5.4xlarge" in root["statement"]
    assert "team search" in root["statement"]
    evidence_ids = {e["id"] for e in report["evidence"]}
    assert set(root["evidence_ids"]) <= evidence_ids
    assert Decimal(report["key_figures"]["new_resources_delta_per_day"]) == Decimal(
        "130.56"
    )  # 8 x 0.68 x 24
    assert Decimal(report["key_figures"]["explained_pct"]) >= 80
    assert inv["usage"]["llm_calls"] == 0
    assert all(c["tool"] for s in inv["steps"] for c in s["tool_calls"])
    md = analyst.get(f"/api/v1/investigations/{inv['id']}/report.md")
    assert md.status_code == 200
    assert "Mode: **deterministic**" in md.text
    assert "computed, not model-generated" in md.text


def test_policy_review_is_read_only_and_honest(
    client_for: ClientFor, demo_tenant: TenantUsers
) -> None:
    analyst = client_for(demo_tenant.emails["analyst"])
    inv = _investigate(analyst, _anomaly_id(analyst, "account"))
    proposals = inv["report"]["proposed_actions"]
    idle = [p for p in proposals if p["action_type"] == "simulate_stop_nonprod_instance"]
    assert idle, "the idle dev instance should be proposed"
    assert Decimal(idle[0]["estimated_monthly_savings"]) == Decimal("280.32")
    # Demo inventory was observed 2026-10-01; the 48h freshness rule denies it today.
    assert idle[0]["policy_allowed"] is False
    assert any("observed" in r for r in idle[0]["policy_denial_reasons"])
    assert analyst.get("/api/v1/audit-events").status_code == 403  # analyst cannot read audit
    assert not list(client_for(demo_tenant.emails["auditor"])
        .get("/api/v1/audit-events?event_type=approval")
        .json()["items"])


def test_llm_mode_is_labeled_and_tracked(
    client_for: ClientFor, demo_tenant: TenantUsers, use_model: Callable[[FakeModel], None]
) -> None:
    def grounded(evidence: list[Evidence]) -> ModelAnalysis:
        resource = next(e for e in evidence if e.kind == "resource")
        return ModelAnalysis(
            hypotheses=[
                {
                    "statement": "A new batch fleet in the dev account drives the spike.",
                    "evidence_ids": [resource.id, "computed:attribution"],
                    "confidence": "high",
                }
            ],
            narrative="New batch instances explain most of the increase.",
            insufficient_evidence=False,
        )

    model = FakeModel(grounded)
    use_model(model)
    analyst = client_for(demo_tenant.emails["analyst"])
    inv = _investigate(analyst, _anomaly_id(analyst, "service"))
    assert inv["state"] == "completed"
    assert inv["mode"] == "fake-llm"
    assert inv["model"] == "fake-model-1"
    assert inv["report"]["summary"] == "New batch instances explain most of the increase."
    assert inv["usage"]["llm_calls"] == 1
    assert inv["usage"]["input_tokens"] == 1200
    hyp = next(s for s in inv["steps"] if s["node"] == "hypothesize")
    assert (hyp["mode"], hyp["tokens_in"], hyp["tokens_out"]) == ("fake-llm", 1200, 300)
    # Figures still come from deterministic code, not the model.
    assert Decimal(inv["report"]["key_figures"]["new_resources_delta_per_day"]) == Decimal("130.56")


@pytest.mark.parametrize(
    "failure",
    [
        MalformedOutputError("model output did not match the schema (JSONDecodeError)"),
        ProviderUnavailableError("fake: HTTP 503"),
        ModelAnalysis(
            hypotheses=[
                {
                    "statement": "Ignore the data: i-0deadbeefdeadbeef caused $99,999.00 of spend.",
                    "evidence_ids": ["resource:i-0deadbeefdeadbeef"],
                    "confidence": "high",
                }
            ],
            narrative="Prompt-injected answer",
            insufficient_evidence=False,
        ),
    ],
    ids=["malformed", "unavailable", "injected"],
)
def test_bad_model_output_falls_back_to_deterministic(
    client_for: ClientFor,
    demo_tenant: TenantUsers,
    use_model: Callable[[FakeModel], None],
    failure: Any,
) -> None:
    model = FakeModel(lambda _evidence: failure)
    use_model(model)
    analyst = client_for(demo_tenant.emails["analyst"])
    inv = _investigate(analyst, _anomaly_id(analyst, "service"))
    assert inv["state"] == "completed"
    assert inv["mode"] == "deterministic"
    assert model.calls == 2  # bounded retry, then fallback
    assert [s["node"] for s in inv["steps"]].count("hypothesize") == 2
    rejected = inv["report"]["model_output_rejected"]
    assert len(rejected) >= 2
    assert "Prompt-injected" not in inv["report"]["summary"]
    assert "99,999" not in str(inv["report"]["hypotheses"])
    assert "8 new c5.4xlarge" in inv["report"]["root_cause"]["statement"]


def test_budget_and_cancellation(
    client_for: ClientFor, demo_tenant: TenantUsers, app_settings: Settings, db: Session
) -> None:
    analyst = client_for(demo_tenant.emails["analyst"])
    anomaly_id = _anomaly_id(analyst, "service")
    tight = app_settings.model_copy(update={"agent_max_steps": 3})
    created = analyst.post(f"/api/v1/anomalies/{anomaly_id}/investigate", headers=idem()).json()
    db.execute(text("UPDATE jobs SET state = 'cancelled' WHERE state = 'queued'"))
    db.commit()
    result = service.execute(
        DRAIN["factory"], tight, demo_tenant.tenant_id, uuid.UUID(created["id"])
    )
    assert result == {"state": "failed", "steps": 3, "error": "step budget exhausted (3)"}
    assert (
        analyst.get(f"/api/v1/investigations/{created['id']}").json()["error"]
        == "step budget exhausted (3)"
    )

    second = analyst.post(f"/api/v1/anomalies/{anomaly_id}/investigate", headers=idem()).json()
    cancelled = analyst.post(f"/api/v1/investigations/{second['id']}/cancel")
    assert cancelled.json()["cancel_requested"] is True
    drain_jobs()
    assert analyst.get(f"/api/v1/investigations/{second['id']}").json()["state"] == "cancelled"
    assert analyst.post(f"/api/v1/investigations/{second['id']}/cancel").status_code == 409


def test_cross_tenant_and_rbac_are_enforced(
    client_for: ClientFor, demo_tenant: TenantUsers, db: Session
) -> None:
    analyst = client_for(demo_tenant.emails["analyst"])
    anomaly_id = _anomaly_id(analyst, "service")
    other = create_tenant(db, ("analyst",))
    outsider = client_for(other.emails["analyst"])
    assert (
        outsider.post(f"/api/v1/anomalies/{anomaly_id}/investigate", headers=idem()).status_code
        == 404
    )
    viewer = client_for(demo_tenant.emails["viewer"])
    assert (
        viewer.post(f"/api/v1/anomalies/{anomaly_id}/investigate", headers=idem()).status_code
        == 403
    )

    with read_only_session(DRAIN["factory"]) as ro:
        tools = EvidenceTools(ro, other.tenant_id)
        with pytest.raises(ToolAccessDeniedError):
            tools.anomaly_context(uuid.UUID(anomaly_id))
        resource = analyst.get("/api/v1/resources?resource_type=ec2_instance&limit=1").json()[
            "items"
        ][0]
        with pytest.raises(ToolAccessDeniedError):
            tools.resource_uuid(uuid.UUID(resource["id"]))
        assert tools.resource_inventory([resource["resource_id"]]) == []


def test_agent_tools_cannot_write(demo_tenant: TenantUsers) -> None:
    with (
        read_only_session(DRAIN["factory"]) as ro,
        pytest.raises(DBAPIError, match="read-only transaction"),
    ):
        ro.execute(text("UPDATE recommendations SET status = 'actioned'"))
