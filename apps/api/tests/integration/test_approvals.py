"""Phase 6: approvals, policy, simulated remediation, and their failure modes."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text, update
from sqlalchemy.orm import Session

from nimbrix_api.models import ApprovalRequest, Job, ResourceInventory
from nimbrix_api.modules.jobs import queue
from nimbrix_api.modules.remediation import service
from nimbrix_api.modules.remediation.registry import PolicyResult
from tests.integration.conftest import DRAIN, TenantUsers, drain_jobs, idem

ClientFor = Callable[..., TestClient]
IDLE = "dev-build-runner-legacy"


def _resource(client: TestClient, name: str) -> dict[str, Any]:
    items = client.get(f"/api/v1/resources?q={name}&resource_type=ec2_instance").json()["items"]
    assert len(items) == 1, items
    resource: dict[str, Any] = items[0]
    return resource


def _payload(resource: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "resource_id": resource["resource_id"],
        "account_id": resource["account_id"],
        "region": resource["region"],
        "reason": "idle for 14 days",
        **extra,
    }


def _request(client: TestClient, resource: dict[str, Any], **body: Any) -> Any:
    return client.post(
        "/api/v1/approvals",
        json={
            "action_type": "simulate_stop_nonprod_instance",
            "payload": _payload(resource),
            **body,
        },
        headers=idem(),
    )


@pytest.fixture
def tenant(fresh_demo_tenant: TenantUsers, db: Session) -> TenantUsers:
    # Each test starts with fresh inventory and no live approvals on the idle instance.
    db.execute(
        update(ResourceInventory)
        .where(ResourceInventory.tenant_id == fresh_demo_tenant.tenant_id)
        .values(observed_at=datetime.now(UTC), state="running")
    )
    db.execute(
        update(ApprovalRequest)
        .where(
            ApprovalRequest.tenant_id == fresh_demo_tenant.tenant_id,
            ApprovalRequest.status.in_(("pending", "approved")),
        )
        .values(status="cancelled")
    )
    db.execute(
        text(
            "UPDATE recommendations SET status = 'open' WHERE tenant_id = :t AND status = 'approval_requested'"
        ),
        {"t": fresh_demo_tenant.tenant_id},
    )
    db.commit()
    return fresh_demo_tenant


def test_full_simulated_remediation_flow(client_for: ClientFor, tenant: TenantUsers) -> None:
    analyst, approver = client_for(tenant.emails["analyst"]), client_for(tenant.emails["approver"])
    recs = analyst.get("/api/v1/recommendations?category=idle_nonprod_instance").json()["items"]
    rec = next(r for r in recs if r["status"] == "open")
    resource = _resource(analyst, IDLE)

    preview = analyst.post(
        "/api/v1/approvals/preview",
        json={"action_type": "simulate_stop_nonprod_instance", "payload": _payload(resource)},
    ).json()
    assert preview["allowed"] is True
    assert preview["impact_preview"]["estimated_monthly_savings"] == "280.32"

    created = _request(analyst, resource, recommendation_id=rec["id"])
    assert created.status_code == 201, created.text
    approval = created.json()
    assert approval["status"] == "pending"
    assert approval["payload_hash"] == preview["payload_hash"]
    assert approval["rollback_plan"]["steps"]
    assert approval["impact_preview"]["realized"] is False
    assert (
        analyst.get(f"/api/v1/recommendations/{rec['id']}").json()["status"] == "approval_requested"
    )

    approved = approver.post(
        f"/api/v1/approvals/{approval['id']}/approve",
        json={"payload_hash": approval["payload_hash"], "reason": "dev box unused"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["decided_by_email"] == tenant.emails["approver"]

    executed = analyst.post(
        "/api/v1/remediations", json={"approval_request_id": approval["id"]}, headers=idem()
    )
    assert executed.status_code == 202, executed.text
    assert executed.json()["mode"] == "simulation"
    assert executed.json()["dry_run"] is True
    drain_jobs()
    outcome = analyst.get(f"/api/v1/remediations/{executed.json()['id']}").json()
    assert outcome["state"] == "succeeded"
    assert outcome["result"]["before"] == {"state": "running", "monthly_compute_cost": "280.32"}
    assert outcome["result"]["after"] == {"state": "stopped", "monthly_compute_cost": "0.00"}
    assert outcome["result"]["realized"] is False
    assert outcome["result"]["cloud_api_called"] is False
    assert outcome["verification"]["verified"] is True
    assert analyst.get(f"/api/v1/approvals/{approval['id']}").json()["status"] == "executed"
    assert analyst.get(f"/api/v1/recommendations/{rec['id']}").json()["status"] == "actioned"
    assert _resource(analyst, IDLE)["state"] == "running"  # observed inventory is never rewritten

    trail = (
        client_for(tenant.emails["auditor"])
        .get(f"/api/v1/audit-events?target_id={approval['id']}")
        .json()["items"]
    )
    assert [e["event_type"] for e in reversed(trail)] == ["approval.requested", "approval.approved"]
    run_trail = (
        client_for(tenant.emails["auditor"])
        .get(f"/api/v1/audit-events?target_id={executed.json()['id']}")
        .json()["items"]
    )
    assert [e["event_type"] for e in reversed(run_trail)] == [
        "remediation.requested",
        "remediation.succeeded",
    ]


@pytest.mark.parametrize(
    ("target", "body_patch", "failed_check"),
    [
        ("payments-batch-01", {}, "environment_nonprod"),
        ("dev-vpn-gateway", {}, "not_protected"),
        (IDLE, {"action_type": "terminate_instance"}, "action_registered"),
        (IDLE, {"payload_extra": {"force": True}}, "payload_schema"),
        (IDLE, {"payload_patch": {"region": "eu-west-1"}}, "account_region_match"),
        (IDLE, {"payload_patch": {"resource_id": "i-0notmine0000000"}}, "resource_in_tenant"),
    ],
)
def test_policy_denies_unsafe_requests(
    client_for: ClientFor,
    tenant: TenantUsers,
    target: str,
    body_patch: dict[str, Any],
    failed_check: str,
) -> None:
    analyst = client_for(tenant.emails["analyst"])
    resource = _resource(analyst, target)
    payload = {
        **_payload(resource),
        **body_patch.get("payload_extra", {}),
        **body_patch.get("payload_patch", {}),
    }
    response = analyst.post(
        "/api/v1/approvals",
        json={
            "action_type": body_patch.get("action_type", "simulate_stop_nonprod_instance"),
            "payload": payload,
        },
        headers=idem(),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "policy_denied"
    assert failed_check in {d["field"] for d in response.json()["error"]["details"]}


def test_unauthorized_and_self_approval_are_denied(
    client_for: ClientFor, tenant: TenantUsers
) -> None:
    analyst, admin, viewer = (client_for(tenant.emails[r]) for r in ("analyst", "admin", "viewer"))
    resource = _resource(analyst, IDLE)
    assert _request(viewer, resource).status_code == 403
    approval = _request(admin, resource).json()
    assert (
        analyst.post(
            f"/api/v1/approvals/{approval['id']}/approve",
            json={"payload_hash": approval["payload_hash"]},
        ).status_code
        == 403
    )
    selfie = admin.post(
        f"/api/v1/approvals/{approval['id']}/approve",
        json={"payload_hash": approval["payload_hash"]},
    )
    assert selfie.status_code == 403
    assert selfie.json()["error"]["code"] == "separation_of_duties"
    assert (
        analyst.post(
            "/api/v1/remediations", json={"approval_request_id": approval["id"]}, headers=idem()
        ).json()["error"]["code"]
        == "not_approved"
    )


def test_tampered_payload_is_rejected(
    client_for: ClientFor, tenant: TenantUsers, db: Session
) -> None:
    analyst, approver = client_for(tenant.emails["analyst"]), client_for(tenant.emails["approver"])
    approval = _request(analyst, _resource(analyst, IDLE)).json()
    wrong = approver.post(
        f"/api/v1/approvals/{approval['id']}/approve", json={"payload_hash": "0" * 64}
    )
    assert wrong.json()["error"]["code"] == "payload_hash_mismatch"
    assert approver.post(f"/api/v1/approvals/{approval['id']}/approve", json={}).status_code == 422
    db.execute(
        text(
            "UPDATE approval_requests SET payload = jsonb_set(payload, '{resource_id}', '\"i-0prod\"') WHERE id = :i"
        ),
        {"i": approval["id"]},
    )
    db.commit()
    tampered = approver.post(
        f"/api/v1/approvals/{approval['id']}/approve",
        json={"payload_hash": approval["payload_hash"]},
    )
    assert tampered.status_code == 409
    assert tampered.json()["error"]["code"] == "payload_tampered"


def test_expired_approval(client_for: ClientFor, tenant: TenantUsers, db: Session) -> None:
    analyst, approver = client_for(tenant.emails["analyst"]), client_for(tenant.emails["approver"])
    approval = _request(analyst, _resource(analyst, IDLE)).json()
    db.execute(
        update(ApprovalRequest)
        .where(ApprovalRequest.id == uuid.UUID(approval["id"]))
        .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    db.commit()
    response = approver.post(
        f"/api/v1/approvals/{approval['id']}/approve",
        json={"payload_hash": approval["payload_hash"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "approval_expired"
    assert analyst.get(f"/api/v1/approvals/{approval['id']}").json()["status"] == "expired"


def _approved(client_for: ClientFor, tenant: TenantUsers) -> tuple[TestClient, dict[str, Any]]:
    analyst, approver = client_for(tenant.emails["analyst"]), client_for(tenant.emails["approver"])
    approval = _request(analyst, _resource(analyst, IDLE)).json()
    approver.post(
        f"/api/v1/approvals/{approval['id']}/approve",
        json={"payload_hash": approval["payload_hash"]},
    )
    return analyst, approval


def test_duplicate_execution_is_prevented(client_for: ClientFor, tenant: TenantUsers) -> None:
    analyst, approval = _approved(client_for, tenant)
    key = idem()
    first = analyst.post(
        "/api/v1/remediations", json={"approval_request_id": approval["id"]}, headers=key
    )
    replay = analyst.post(
        "/api/v1/remediations", json={"approval_request_id": approval["id"]}, headers=key
    )
    assert first.status_code == replay.status_code == 202
    assert first.json()["id"] == replay.json()["id"]
    again = analyst.post(
        "/api/v1/remediations", json={"approval_request_id": approval["id"]}, headers=idem()
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_executed"
    drain_jobs()
    assert (
        analyst.post(
            "/api/v1/remediations", json={"approval_request_id": approval["id"]}, headers=idem()
        ).json()["error"]["code"]
        == "already_executed"
    )


def test_stale_inventory_blocks_execution(
    client_for: ClientFor, tenant: TenantUsers, db: Session
) -> None:
    analyst, approval = _approved(client_for, tenant)
    db.execute(
        update(ResourceInventory)
        .where(ResourceInventory.tenant_id == tenant.tenant_id)
        .values(observed_at=datetime.now(UTC) - timedelta(days=10))
    )
    db.commit()
    response = analyst.post(
        "/api/v1/remediations", json={"approval_request_id": approval["id"]}, headers=idem()
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "revalidation_failed"
    assert "inventory_fresh" in {d["field"] for d in response.json()["error"]["details"]}


def test_state_change_after_request_blocks_approval(
    client_for: ClientFor, tenant: TenantUsers, db: Session
) -> None:
    analyst, approver = client_for(tenant.emails["analyst"]), client_for(tenant.emails["approver"])
    resource = _resource(analyst, IDLE)
    approval = _request(analyst, resource).json()
    db.execute(
        update(ResourceInventory)
        .where(ResourceInventory.id == uuid.UUID(resource["id"]))
        .values(state="stopped")
    )
    db.commit()
    response = approver.post(
        f"/api/v1/approvals/{approval['id']}/approve",
        json={"payload_hash": approval["payload_hash"]},
    )
    assert response.status_code == 409
    assert "state_running" in {d["field"] for d in response.json()["error"]["details"]}


def test_missing_rollback_plan_is_refused(
    client_for: ClientFor, tenant: TenantUsers, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = service.policy_evaluator

    def without_rollback(*args: Any, **kwargs: Any) -> PolicyResult:
        result = original(*args, **kwargs)
        result.rollback_plan = {}
        return result

    monkeypatch.setattr(service, "policy_evaluator", without_rollback)
    analyst = client_for(tenant.emails["analyst"])
    response = _request(analyst, _resource(analyst, IDLE))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "missing_rollback_plan"


def test_rejection_path(client_for: ClientFor, tenant: TenantUsers) -> None:
    analyst, approver = client_for(tenant.emails["analyst"]), client_for(tenant.emails["approver"])
    approval = _request(analyst, _resource(analyst, IDLE)).json()
    assert (
        approver.post(
            f"/api/v1/approvals/{approval['id']}/reject", json={"reason": "x"}
        ).status_code
        == 422
    )
    rejected = approver.post(
        f"/api/v1/approvals/{approval['id']}/reject", json={"reason": "needed for release testing"}
    )
    assert rejected.json()["status"] == "rejected"
    assert (
        approver.post(
            f"/api/v1/approvals/{approval['id']}/approve",
            json={"payload_hash": approval["payload_hash"]},
        ).json()["error"]["code"]
        == "invalid_state"
    )
    assert (
        analyst.post(
            "/api/v1/remediations", json={"approval_request_id": approval["id"]}, headers=idem()
        ).json()["error"]["code"]
        == "not_approved"
    )


def test_worker_crash_and_retry_executes_once(
    client_for: ClientFor, tenant: TenantUsers, db: Session
) -> None:
    analyst, approval = _approved(client_for, tenant)
    db.execute(update(Job).where(Job.state == "queued").values(state="cancelled"))
    db.commit()
    execution = analyst.post(
        "/api/v1/remediations", json={"approval_request_id": approval["id"]}, headers=idem()
    ).json()
    with DRAIN["factory"]() as session:
        claimed = queue.claim_next(session, "worker-that-crashes", lease_seconds=60)
        assert claimed is not None
        assert claimed.kind == "remediate"
        # The worker dies mid-flight after marking the execution running.
        session.execute(
            text("UPDATE remediation_executions SET state = 'running', attempts = 1 WHERE id = :i"),
            {"i": execution["id"]},
        )
        session.execute(
            update(Job)
            .where(Job.id == claimed.id)
            .values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()
    drain_jobs()
    outcome = analyst.get(f"/api/v1/remediations/{execution['id']}").json()
    assert outcome["state"] == "succeeded"
    assert outcome["attempts"] == 2
    with DRAIN["factory"]() as session:
        replay = service.run_execution(
            session, DRAIN["settings"], tenant.tenant_id, uuid.UUID(execution["id"])
        )
        assert replay == {"state": "succeeded", "replayed": True}
        states = session.execute(
            text("SELECT count(*) FROM simulated_resource_states WHERE tenant_id = :t"),
            {"t": tenant.tenant_id},
        ).scalar_one()
        assert states == 1


def test_actions_registry_is_simulation_only(client_for: ClientFor, tenant: TenantUsers) -> None:
    actions = client_for(tenant.emails["viewer"]).get("/api/v1/actions").json()
    assert [a["name"] for a in actions["items"]] == ["simulate_stop_nonprod_instance"]
    assert actions["items"][0]["mode"] == "simulation"
    assert actions["live_actions_enabled"] is False
