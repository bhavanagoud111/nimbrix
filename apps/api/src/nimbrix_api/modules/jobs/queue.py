"""Postgres job queue: enqueue with dedupe, claim with SKIP LOCKED, lease-based recovery."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from nimbrix_api.models import Job

MAX_BACKOFF_SECONDS = 300


class PermanentJobError(Exception):
    """Do not retry: the input is invalid or the operation can never succeed."""


class RetryableJobError(Exception):
    """Transient failure (throttling, network, lock timeout); retry with backoff."""


def enqueue(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    kind: str,
    payload: dict[str, Any],
    created_by: uuid.UUID | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 3,
    run_after: datetime | None = None,
) -> Job:
    """Insert a job; if an active job with the same dedupe key exists, return that job."""
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "kind": kind,
        "payload": payload,
        "state": "queued",
        "attempts": 0,
        "max_attempts": max_attempts,
        "dedupe_key": dedupe_key,
        "created_by": created_by,
        "run_after": run_after or datetime.now(UTC),
    }
    stmt = (
        insert(Job)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "dedupe_key"],
            index_where=text("state IN ('queued', 'running') AND dedupe_key IS NOT NULL"),
        )
        .returning(Job.id)
    )
    inserted_id = session.execute(stmt).scalar_one_or_none()
    if inserted_id is not None:
        job = session.get(Job, inserted_id)
        assert job is not None  # noqa: S101
        return job
    existing = session.scalar(
        select(Job).where(
            Job.tenant_id == tenant_id,
            Job.dedupe_key == dedupe_key,
            Job.state.in_(("queued", "running")),
        )
    )
    assert existing is not None  # noqa: S101 - conflict implies an active row
    return existing


def claim_next(session: Session, worker_id: str, lease_seconds: int) -> Job | None:
    """Claim one runnable job (or one whose lease expired after a worker crash)."""
    now = datetime.now(UTC)
    job = session.scalar(
        select(Job)
        .where(
            or_(
                (Job.state == "queued") & (Job.run_after <= now),
                (Job.state == "running") & (Job.locked_until < now),
            )
        )
        .order_by(Job.run_after, Job.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    if job.state == "running" and job.attempts >= job.max_attempts:
        # Crashed on its final attempt: fail it instead of looping forever.
        job.state = "failed"
        job.error = "worker lease expired on the final attempt"
        job.finished_at = now
        job.locked_by = None
        job.locked_until = None
        session.commit()
        return claim_next(session, worker_id, lease_seconds)
    job.state = "running"
    job.attempts += 1
    job.locked_by = worker_id
    job.locked_until = now + timedelta(seconds=lease_seconds)
    job.started_at = job.started_at or now
    session.commit()
    return job


def heartbeat(session: Session, job_id: uuid.UUID, worker_id: str, lease_seconds: int) -> None:
    job = session.get(Job, job_id, with_for_update=True)
    if job is not None and job.state == "running" and job.locked_by == worker_id:
        job.locked_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
    session.commit()


def complete(session: Session, job_id: uuid.UUID, result: dict[str, Any]) -> None:
    job = session.get(Job, job_id, with_for_update=True)
    if job is None:
        return
    job.state = "succeeded"
    job.result = result
    job.error = None
    job.finished_at = datetime.now(UTC)
    job.locked_by = None
    job.locked_until = None
    session.commit()


def fail(session: Session, job_id: uuid.UUID, error: str, *, retryable: bool) -> str:
    """Record a failure. Returns the resulting state ('queued' for retry or 'failed')."""
    job = session.get(Job, job_id, with_for_update=True)
    if job is None:
        return "failed"
    now = datetime.now(UTC)
    job.error = error[:2000]
    job.locked_by = None
    job.locked_until = None
    if retryable and job.attempts < job.max_attempts:
        job.state = "queued"
        job.run_after = now + timedelta(seconds=min(2**job.attempts, MAX_BACKOFF_SECONDS))
    else:
        job.state = "failed"
        job.finished_at = now
    session.commit()
    return job.state
