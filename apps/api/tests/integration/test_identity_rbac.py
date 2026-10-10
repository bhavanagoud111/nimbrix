"""Phase 1: authentication, RBAC, CSRF, idempotency, tenant isolation, audit."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from nimbrix_api.models import AuditEvent
from nimbrix_api.models import Session as SessionRow
from tests.integration.conftest import DEMO_PASSWORD, idem

ClientFor = Callable[..., TestClient]
ACCOUNT = "123456789012"


def _connection_body(name: str, account: str = ACCOUNT) -> dict[str, object]:
    return {
        "name": name,
        "account_id": account,
        "role_arn": f"arn:aws:iam::{account}:role/NimbrixReadOnly",
        "external_id": "nimbrix-external-id",
        "regions": ["us-east-1", "eu-west-1"],
    }


@pytest.mark.parametrize(
    ("email", "role"),
    [
        ("viewer@acme.demo", "viewer"),
        ("analyst@acme.demo", "analyst"),
        ("approver@acme.demo", "approver"),
        ("auditor@acme.demo", "auditor"),
        ("admin@acme.demo", "admin"),
    ],
)
def test_login_binds_role_and_tenant(client_for: ClientFor, email: str, role: str) -> None:
    client = client_for(email)
    me = client.get("/api/v1/me").json()
    assert me["role"] == role
    assert me["tenant"]["slug"] == "acme-demo"
    assert me["auth_method"] == "demo-session:cookie"
    assert ("audit:read" in me["permissions"]) == (role in {"approver", "auditor", "admin"})


def test_login_sets_hardened_cookies(client_for: ClientFor) -> None:
    client = client_for()
    response = client.post(
        "/api/v1/auth/login", json={"email": "viewer@acme.demo", "password": DEMO_PASSWORD}
    )
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("nimbrix_session="))
    csrf_cookie = next(c for c in cookies if c.startswith("nimbrix_csrf="))
    assert "HttpOnly" in session_cookie
    assert "samesite=lax" in session_cookie.lower()
    assert "HttpOnly" not in csrf_cookie
    assert "nimbrix_session" not in response.text  # token never in the body


def test_invalid_credentials_are_rejected_and_audited(client_for: ClientFor, db: Session) -> None:
    client = client_for()
    for email in ("viewer@acme.demo", "nobody@acme.demo"):
        response = client.post(
            "/api/v1/auth/login", json={"email": email, "password": "wrong-pass"}
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_credentials"
    events = db.scalars(
        select(AuditEvent).where(
            AuditEvent.event_type == "auth.login_failed",
            AuditEvent.actor_label == "nobody@acme.demo",
        )
    ).all()
    assert events
    assert all("wrong-pass" not in str(e.payload) for e in events)


def test_unauthenticated_requests_fail_closed(client_for: ClientFor) -> None:
    client = client_for()
    for path in (
        "/api/v1/me",
        "/api/v1/tenants/current",
        "/api/v1/connections",
        "/api/v1/audit-events",
    ):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "unauthenticated"


def test_logout_revokes_session(client_for: ClientFor) -> None:
    client = client_for("viewer@acme.demo")
    assert client.post("/api/v1/auth/logout").status_code == 204
    client.cookies.clear()
    assert client.get("/api/v1/me").status_code == 401


def test_revoked_session_token_cannot_be_reused(client_for: ClientFor) -> None:
    client = client_for("viewer@acme.demo")
    token = client.cookies.get("nimbrix_session")
    assert client.post("/api/v1/auth/logout").status_code == 204
    other = client_for()
    assert other.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_expired_session_is_rejected(client_for: ClientFor, db: Session) -> None:
    client = client_for("viewer@acme.demo")
    db.execute(
        update(SessionRow)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        .where(SessionRow.revoked_at.is_(None), SessionRow.user_agent.is_not(None))
    )
    db.commit()
    assert client.get("/api/v1/me").status_code == 401


def test_csrf_required_for_cookie_mutations(client_for: ClientFor) -> None:
    client = client_for("admin@acme.demo")
    csrf = client.headers.pop("X-CSRF-Token")
    response = client.post("/api/v1/connections", json=_connection_body("csrf"), headers=idem())
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"
    response = client.post(
        "/api/v1/connections",
        json=_connection_body("csrf", "210987654321"),
        headers={**idem(), "X-CSRF-Token": "forged-token-value"},
    )
    assert response.status_code == 403
    response = client.post(
        "/api/v1/connections",
        json=_connection_body("csrf", "210987654321"),
        headers={**idem(), "X-CSRF-Token": csrf},
    )
    assert response.status_code == 201


def test_bearer_session_does_not_need_csrf(client_for: ClientFor) -> None:
    client = client_for("admin@acme.demo")
    token = client.cookies.get("nimbrix_session")
    bare = client_for()
    response = bare.post(
        "/api/v1/connections",
        json=_connection_body("bearer", "333333333333"),
        headers={**idem(), "Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201


@pytest.mark.parametrize("email", ["viewer@acme.demo", "analyst@acme.demo", "auditor@acme.demo"])
def test_rbac_denies_connection_writes_and_audits(
    client_for: ClientFor, db: Session, email: str
) -> None:
    client = client_for(email)
    response = client.post("/api/v1/connections", json=_connection_body("x"), headers=idem())
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    denied = db.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "authz.denied", AuditEvent.actor_label == email)
        .order_by(AuditEvent.occurred_at.desc())
    )
    assert denied is not None
    assert denied.payload["required_permission"] == "connections:write"


def test_rbac_audit_read(client_for: ClientFor) -> None:
    assert client_for("viewer@acme.demo").get("/api/v1/audit-events").status_code == 403
    assert client_for("analyst@acme.demo").get("/api/v1/audit-events").status_code == 403
    assert client_for("auditor@acme.demo").get("/api/v1/audit-events").status_code == 200


def test_idempotency_replays_and_detects_reuse(client_for: ClientFor) -> None:
    client = client_for("admin@acme.demo")
    headers = idem()
    body = _connection_body("idem", "444444444444")
    first = client.post("/api/v1/connections", json=body, headers=headers)
    second = client.post("/api/v1/connections", json=body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    reused = client.post("/api/v1/connections", json={**body, "name": "different"}, headers=headers)
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "idempotency_key_reused"
    missing = client.post("/api/v1/connections", json=body)
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "idempotency_key_required"


def test_connections_never_accept_credentials(client_for: ClientFor) -> None:
    client = client_for("admin@acme.demo")
    with_key = {**_connection_body("k", "555555555555"), "aws_secret_access_key": "x" * 40}
    assert client.post("/api/v1/connections", json=with_key, headers=idem()).status_code == 422
    key_as_external_id = {
        **_connection_body("k", "555555555555"),
        "external_id": "AKIAABCDEFGHIJKLMNOP",
    }
    response = client.post("/api/v1/connections", json=key_as_external_id, headers=idem())
    assert response.status_code == 422
    assert "AKIAABCDEFGHIJKLMNOP" not in response.text
    mismatch = {
        **_connection_body("k", "555555555555"),
        "role_arn": "arn:aws:iam::999999999999:role/X",
    }
    assert client.post("/api/v1/connections", json=mismatch, headers=idem()).status_code == 422
    created = client.post(
        "/api/v1/connections", json=_connection_body("ok", "555555555555"), headers=idem()
    )
    assert created.status_code == 201
    assert created.json()["has_external_id"] is True
    assert "external_id" not in created.json()


def test_tenant_isolation_fails_closed(client_for: ClientFor, db: Session) -> None:
    globex = client_for("admin@globex.demo")
    acme = client_for("admin@acme.demo")
    created = globex.post(
        "/api/v1/connections", json=_connection_body("globex-only", "666666666666"), headers=idem()
    )
    assert created.status_code == 201
    globex_id = created.json()["id"]

    # Listing never includes another tenant's rows.
    assert globex_id not in {c["id"] for c in acme.get("/api/v1/connections").json()["items"]}
    # Direct access by ID is indistinguishable from "missing" and is audited.
    response = acme.get(f"/api/v1/connections/{globex_id}")
    assert response.status_code == 404
    assert globex.get(f"/api/v1/connections/{globex_id}").status_code == 200
    attempt = db.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == "security.cross_tenant_access_denied",
            AuditEvent.target_id == globex_id,
        )
    )
    assert attempt is not None
    assert attempt.actor_label == "admin@acme.demo"
    # A client-supplied tenant header is ignored; the session decides.
    me = acme.get("/api/v1/me", headers={"X-Tenant-ID": created.json()["id"]}).json()
    assert me["tenant"]["slug"] == "acme-demo"
    # Audit trail is tenant-scoped too.
    events = globex.get("/api/v1/audit-events?event_type=security.").json()["items"]
    assert all(e["target_id"] != globex_id for e in events)


def test_audit_trail_records_and_paginates(client_for: ClientFor) -> None:
    admin = client_for("admin@acme.demo")
    admin.post(
        "/api/v1/connections", json=_connection_body("audit", "777777777777"), headers=idem()
    )
    first = admin.get("/api/v1/audit-events?limit=2").json()
    assert len(first["items"]) == 2
    assert first["page"]["next_cursor"]
    second = admin.get(f"/api/v1/audit-events?limit=2&cursor={first['page']['next_cursor']}").json()
    assert {e["id"] for e in first["items"]}.isdisjoint({e["id"] for e in second["items"]})
    created = admin.get("/api/v1/audit-events?event_type=connection.created").json()["items"]
    assert any(e["payload"].get("account_id") == "777777777777" for e in created)
    assert admin.get("/api/v1/audit-events?cursor=garbage!").status_code == 400


def test_audit_events_are_append_only(db: Session) -> None:
    for statement in ("UPDATE audit_events SET outcome = 'tampered'", "DELETE FROM audit_events"):
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text(statement))
        db.rollback()
