"""Phase 2: demo + CSV ingestion end to end, idempotency, job queue semantics, AWS stubs."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from nimbrix_api.api.v1 import connections as connections_api
from nimbrix_api.core.config import Settings, get_settings
from nimbrix_api.models import CostLineItem, Job, Tenant
from nimbrix_api.modules.ingestion import jobs as ingestion_jobs
from nimbrix_api.modules.ingestion import synthetic
from nimbrix_api.modules.jobs import queue, runner
from nimbrix_api.modules.jobs.queue import PermanentJobError, RetryableJobError
from tests.aws_stubs import StubbedSession
from tests.fixtures import fixture_bytes
from tests.integration.conftest import idem

ClientFor = Callable[..., TestClient]
DEMO_END = date(2026, 9, 30)


@pytest.fixture
def run_jobs(app_engine: Engine, app_settings: Settings) -> Callable[[], int]:
    factory = sessionmaker(bind=app_engine, expire_on_commit=False)
    return lambda: runner.drain(factory, app_settings)


def _upload(client: TestClient, name: str, content: bytes | None = None) -> Any:
    return client.post(
        "/api/v1/imports/upload",
        files={"file": (name, content if content is not None else fixture_bytes(name), "text/csv")},
        headers=idem(),
    )


def test_demo_import_end_to_end(
    client_for: ClientFor, run_jobs: Callable[[], int], db: Session
) -> None:
    analyst = client_for("analyst@acme.demo")
    response = analyst.post(
        "/api/v1/imports/demo",
        json={"end_date": str(DEMO_END), "days": 120, "seed": 42},
        headers=idem(),
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["state"] == "queued"
    assert body["mode"] == "demo"
    assert body["job_id"]
    assert analyst.get(f"/api/v1/jobs/{body['job_id']}").json()["state"] == "queued"

    assert run_jobs() >= 1
    done = analyst.get(f"/api/v1/imports/{body['id']}").json()
    expected = len(synthetic.generate(DEMO_END, 120, 42).cost_records)
    assert done["state"] == "succeeded"
    assert done["rows_received"] == expected
    assert done["rows_inserted"] == expected
    assert done["rows_rejected"] == 0
    assert done["period_start"] == str(DEMO_END - timedelta(days=119))
    assert done["period_end"] == str(DEMO_END)
    assert done["resources_upserted"] > 40
    assert analyst.get(f"/api/v1/jobs/{body['job_id']}").json()["state"] == "succeeded"

    tenant_id = db.scalar(select(Tenant.id).where(Tenant.slug == "acme-demo"))
    count = db.scalar(
        select(func.count()).select_from(CostLineItem).where(CostLineItem.tenant_id == tenant_id)
    )
    assert count == expected

    resources = analyst.get(
        "/api/v1/resources?q=dev-build-runner&resource_type=ec2_instance"
    ).json()["items"]
    assert len(resources) == 1
    assert resources[0]["mode"] == "demo"
    detail = analyst.get(f"/api/v1/resources/{resources[0]['id']}").json()
    assert len(detail["daily_cost"]) == 30
    assert detail["cost_last_30_days"] == "276.480000"  # m5.2xlarge $0.384 x 24h x 30d

    # Identical request => duplicate, no new job, no new rows.
    again = analyst.post(
        "/api/v1/imports/demo",
        json={"end_date": str(DEMO_END), "days": 120, "seed": 42},
        headers=idem(),
    )
    assert again.status_code == 200
    assert again.json()["state"] == "duplicate"
    assert again.json()["duplicate_of"] == body["id"]

    # Next day's run: one new day of rows, everything else unchanged.
    next_day = analyst.post(
        "/api/v1/imports/demo",
        json={"end_date": str(DEMO_END + timedelta(days=1)), "days": 120, "seed": 42},
        headers=idem(),
    )
    assert next_day.status_code == 202
    run_jobs()
    shifted = analyst.get(f"/api/v1/imports/{next_day.json()['id']}").json()
    assert shifted["state"] == "succeeded"
    assert shifted["rows_inserted"] > 0
    assert shifted["rows_unchanged"] > shifted["rows_inserted"] * 50
    assert (
        shifted["rows_inserted"] + shifted["rows_updated"] + shifted["rows_unchanged"]
        == shifted["rows_received"]
    )


def test_viewer_cannot_import(client_for: ClientFor) -> None:
    viewer = client_for("viewer@acme.demo")
    assert viewer.post("/api/v1/imports/demo", json={}, headers=idem()).status_code == 403
    assert _upload(viewer, "billing_valid.csv").status_code == 403


def test_csv_lifecycle(client_for: ClientFor, run_jobs: Callable[[], int]) -> None:
    client = client_for("admin@globex.demo")
    valid = _upload(client, "billing_valid.csv")
    assert valid.status_code == 202
    run_jobs()
    first = client.get(f"/api/v1/imports/{valid.json()['id']}").json()
    assert first["state"] == "succeeded"
    assert (first["rows_inserted"], first["rows_rejected"]) == (8, 0)

    duplicate = _upload(client, "billing_valid.csv")
    assert duplicate.status_code == 200
    assert duplicate.json()["state"] == "duplicate"

    late = _upload(client, "billing_late_arriving.csv")
    run_jobs()
    corrected = client.get(f"/api/v1/imports/{late.json()['id']}").json()
    assert corrected["state"] == "succeeded"
    assert (corrected["rows_inserted"], corrected["rows_updated"], corrected["rows_unchanged"]) == (
        1,
        1,
        1,
    )

    malformed = _upload(client, "billing_malformed.csv")
    run_jobs()
    partial = client.get(f"/api/v1/imports/{malformed.json()['id']}").json()
    assert partial["state"] == "partially_failed"
    assert partial["rows_rejected"] == 10
    assert {e["code"] for e in partial["errors"]} >= {"invalid_timestamp", "duplicate_in_file"}

    empty = _upload(client, "billing_empty.csv")
    run_jobs()
    failed = client.get(f"/api/v1/imports/{empty.json()['id']}").json()
    assert failed["state"] == "failed"
    assert failed["errors"][0]["code"] == "empty_dataset"

    not_utf8 = _upload(client, "billing_not_utf8.csv")
    run_jobs()
    rejected = client.get(f"/api/v1/imports/{not_utf8.json()['id']}").json()
    assert rejected["state"] == "failed"
    assert "UTF-8" in rejected["errors"][0]["message"]

    assert _upload(client, "zero.csv", b"   ").status_code == 422


def test_upload_size_limit(app_settings: Settings, client_for: ClientFor) -> None:
    client = client_for("admin@globex.demo")
    small = app_settings.model_copy(update={"upload_max_bytes": 2048})
    client.app.dependency_overrides[get_settings] = lambda: small  # type: ignore[attr-defined]
    big = b"line_item_id\n" + b"x" * 4096
    response = _upload(client, "big.csv", big)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "upload_too_large"


def test_imports_are_tenant_scoped(client_for: ClientFor) -> None:
    globex = client_for("admin@globex.demo")
    acme = client_for("admin@acme.demo")
    created = _upload(globex, "billing_mixed_currency.csv").json()
    assert acme.get(f"/api/v1/imports/{created['id']}").status_code == 404
    assert acme.get(f"/api/v1/jobs/{created['job_id']}").status_code == 404
    assert created["id"] not in {i["id"] for i in acme.get("/api/v1/imports").json()["items"]}


# --- Job queue semantics ---------------------------------------------------------------


@pytest.fixture
def factory(app_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=app_engine, expire_on_commit=False)


@pytest.fixture
def tenant_id(db: Session) -> uuid.UUID:
    # Queue tests need an otherwise idle queue: park anything left by earlier tests.
    db.execute(update(Job).where(Job.state.in_(("queued", "running"))).values(state="cancelled"))
    db.commit()
    value = db.scalar(select(Tenant.id).where(Tenant.slug == "globex-demo"))
    assert value is not None
    return value


@pytest.fixture
def flaky_handler() -> dict[str, int]:
    calls = {"retryable": 0, "permanent": 0}

    def retryable(_: runner.JobContext) -> dict[str, Any]:
        calls["retryable"] += 1
        raise RetryableJobError("simulated throttling")

    def permanent(_: runner.JobContext) -> dict[str, Any]:
        calls["permanent"] += 1
        raise PermanentJobError("bad input")

    runner.register("test_retryable")(retryable)
    runner.register("test_permanent")(permanent)
    return calls


def test_retryable_failures_back_off_then_fail(
    factory: sessionmaker[Session],
    app_settings: Settings,
    tenant_id: uuid.UUID,
    flaky_handler: dict[str, int],
) -> None:
    with factory() as session:
        job = queue.enqueue(
            session, tenant_id=tenant_id, kind="test_retryable", payload={}, max_attempts=2
        )
        session.commit()
    runner.run_one(factory, app_settings, "w1")
    with factory() as session:
        row = session.get(Job, job.id)
        assert row is not None
        assert (row.state, row.attempts) == ("queued", 1)
        assert row.run_after > datetime.now(UTC)  # backoff
        row.run_after = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    runner.run_one(factory, app_settings, "w1")
    with factory() as session:
        row = session.get(Job, job.id)
        assert row is not None
        assert (row.state, row.attempts) == ("failed", 2)
        assert "simulated throttling" in (row.error or "")
    assert flaky_handler["retryable"] == 2


def test_permanent_failures_do_not_retry(
    factory: sessionmaker[Session],
    app_settings: Settings,
    tenant_id: uuid.UUID,
    flaky_handler: dict[str, int],
) -> None:
    with factory() as session:
        job = queue.enqueue(session, tenant_id=tenant_id, kind="test_permanent", payload={})
        session.commit()
    runner.run_one(factory, app_settings, "w1")
    with factory() as session:
        row = session.get(Job, job.id)
        assert row is not None
        assert (row.state, row.attempts) == ("failed", 1)


def test_dedupe_key_prevents_duplicate_active_jobs(
    factory: sessionmaker[Session], tenant_id: uuid.UUID
) -> None:
    key = f"dedupe-{uuid.uuid4().hex}"
    with factory() as session:
        first = queue.enqueue(session, tenant_id=tenant_id, kind="noop", payload={}, dedupe_key=key)
        second = queue.enqueue(
            session, tenant_id=tenant_id, kind="noop", payload={"x": 1}, dedupe_key=key
        )
        session.commit()
        assert first.id == second.id
        session.execute(update(Job).where(Job.id == first.id).values(state="cancelled"))
        third = queue.enqueue(session, tenant_id=tenant_id, kind="noop", payload={}, dedupe_key=key)
        session.commit()
        assert third.id != first.id


def test_expired_lease_is_reclaimed_after_worker_crash(
    factory: sessionmaker[Session], tenant_id: uuid.UUID
) -> None:
    with factory() as session:
        job = queue.enqueue(session, tenant_id=tenant_id, kind="crashy", payload={}, max_attempts=2)
        session.commit()
        session.execute(
            update(Job).where(Job.state == "queued", Job.id != job.id).values(state="cancelled")
        )
        session.commit()
    with factory() as session:
        claimed = queue.claim_next(session, "crashed-worker", lease_seconds=60)
        assert claimed is not None
        assert claimed.id == job.id
        # The worker dies: the lease simply runs out.
        session.execute(
            update(Job)
            .where(Job.id == job.id)
            .values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()
    with factory() as session:
        reclaimed = queue.claim_next(session, "healthy-worker", lease_seconds=60)
        assert reclaimed is not None
        assert (reclaimed.id, reclaimed.attempts, reclaimed.locked_by) == (
            job.id,
            2,
            "healthy-worker",
        )
        session.execute(
            update(Job)
            .where(Job.id == job.id)
            .values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()
    with factory() as session:
        queue.claim_next(session, "third-worker", lease_seconds=60)
        row = session.get(Job, job.id)
        assert row is not None
        assert row.state == "failed"
        assert "final attempt" in (row.error or "")


# --- AWS (stubbed; never calls AWS) ------------------------------------------------------


def test_aws_validate_is_disabled_by_default(client_for: ClientFor) -> None:
    admin = client_for("admin@acme.demo")
    created = admin.post(
        "/api/v1/connections",
        json={
            "name": "aws-off",
            "account_id": "818181818181",
            "role_arn": "arn:aws:iam::818181818181:role/R",
        },
        headers=idem(),
    ).json()
    response = admin.post("/api/v1/connections/aws/validate", json={"connection_id": created["id"]})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "aws_disabled"


def test_aws_validate_and_sync_with_stubs(
    app_settings: Settings,
    client_for: ClientFor,
    monkeypatch: pytest.MonkeyPatch,
    app_engine: Engine,
) -> None:
    settings = app_settings.model_copy(update={"aws_enabled": True})

    validate_session = StubbedSession("sts", "ec2")
    validate_session.stub("sts").add_response(
        "get_caller_identity",
        {
            "Account": "828282828282",
            "Arn": "arn:aws:sts::828282828282:assumed-role/R/s",
            "UserId": "u",
        },
    )
    validate_session.stub("ec2").add_response("describe_instances", {"Reservations": []})
    sync_session = StubbedSession("ce", "ec2", "cloudwatch")
    sync_session.stub("ce").add_response(
        "get_cost_and_usage",
        {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-09-01", "End": "2026-09-02"},
                    "Total": {},
                    "Groups": [
                        {
                            "Keys": ["AmazonEC2", "828282828282"],
                            "Metrics": {"UnblendedCost": {"Amount": "4.2", "Unit": "USD"}},
                        }
                    ],
                    "Estimated": True,
                }
            ],
            "DimensionValueAttributes": [],
        },
    )
    sync_session.stub("ec2").add_response("describe_instances", {"Reservations": []})
    sync_session.stub("ec2").add_response("describe_volumes", {"Volumes": []})
    sync_session.stub("ec2").add_response("describe_addresses", {"Addresses": []})

    class FakeFactory:
        def __init__(self, session: StubbedSession) -> None:
            self.session = session

        def for_connection(self, _: object) -> StubbedSession:
            return self.session

    monkeypatch.setattr(connections_api, "aws_factory", lambda _s: FakeFactory(validate_session))
    monkeypatch.setattr(
        ingestion_jobs, "aws_session_factory", lambda _ctx: FakeFactory(sync_session)
    )

    admin = client_for("admin@acme.demo")
    admin.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
    created = admin.post(
        "/api/v1/connections",
        json={
            "name": "aws-on",
            "account_id": "828282828282",
            "role_arn": "arn:aws:iam::828282828282:role/R",
        },
        headers=idem(),
    ).json()
    validated = admin.post(
        "/api/v1/connections/aws/validate", json={"connection_id": created["id"]}
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["status"] == "validated"
    assert validated.json()["credentials_persisted"] is False

    sync = admin.post(
        f"/api/v1/connections/{created['id']}/sync",
        json={"start": "2026-09-01", "end": "2026-09-02"},
        headers=idem(),
    )
    assert sync.status_code == 202, sync.text
    runner.drain(sessionmaker(bind=app_engine, expire_on_commit=False), settings)
    imported = admin.get(f"/api/v1/imports/{sync.json()['import_id']}").json()
    assert imported["state"] == "succeeded"
    assert imported["mode"] == "live"
    assert imported["details"]["estimated_records"] == 1
