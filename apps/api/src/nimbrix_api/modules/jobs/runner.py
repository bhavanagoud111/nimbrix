"""Job execution: handler registry, single-job runner, and the worker loop."""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Histogram, start_http_server
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from nimbrix_api.core.config import Settings
from nimbrix_api.core.telemetry import tracer
from nimbrix_api.modules.jobs import queue
from nimbrix_api.modules.jobs.queue import PermanentJobError, RetryableJobError

logger = logging.getLogger(__name__)

SCHEDULE_INTERVAL_SECONDS = 300
RETENTION_INTERVAL_SECONDS = 24 * 3600
WORKER_REGISTRY = CollectorRegistry(auto_describe=True)
JOBS_PROCESSED = Counter(
    "nimbrix_jobs_processed_total",
    "Jobs finished by the worker",
    labelnames=("kind", "outcome"),
    registry=WORKER_REGISTRY,
)
JOB_DURATION = Histogram(
    "nimbrix_job_duration_seconds",
    "Job execution time",
    labelnames=("kind",),
    registry=WORKER_REGISTRY,
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
)


@dataclass
class JobContext:
    job_id: uuid.UUID
    tenant_id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    attempt: int
    max_attempts: int
    created_by: uuid.UUID | None
    settings: Settings
    session_factory: sessionmaker[Session]
    worker_id: str
    extra: dict[str, Any] = field(default_factory=dict)

    def heartbeat(self) -> None:
        with self.session_factory() as session:
            queue.heartbeat(session, self.job_id, self.worker_id, self.settings.job_lease_seconds)

    @property
    def is_final_attempt(self) -> bool:
        return self.attempt >= self.max_attempts

    def enqueue_followup(self, kind: str, payload: dict[str, Any], dedupe_key: str | None) -> None:
        with self.session_factory() as session:
            queue.enqueue(
                session,
                tenant_id=self.tenant_id,
                kind=kind,
                payload=payload,
                created_by=self.created_by,
                dedupe_key=dedupe_key,
            )
            session.commit()


Handler = Callable[[JobContext], dict[str, Any]]
_HANDLERS: dict[str, Handler] = {}


def register(kind: str) -> Callable[[Handler], Handler]:
    def decorator(handler: Handler) -> Handler:
        _HANDLERS[kind] = handler
        return handler

    return decorator


def registered_kinds() -> list[str]:
    return sorted(_HANDLERS)


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def run_one(session_factory: sessionmaker[Session], settings: Settings, worker_id: str) -> bool:
    """Claim and execute one job. Returns False when nothing was runnable."""
    # Handlers register on import; importing here keeps the queue module dependency-free.
    from nimbrix_api.modules.jobs import handlers  # noqa: F401, PLC0415

    with session_factory() as session:
        job = queue.claim_next(session, worker_id, settings.job_lease_seconds)
        if job is None:
            return False
        ctx = JobContext(
            job_id=job.id,
            tenant_id=job.tenant_id,
            kind=job.kind,
            payload=dict(job.payload),
            attempt=job.attempts,
            max_attempts=job.max_attempts,
            created_by=job.created_by,
            settings=settings,
            session_factory=session_factory,
            worker_id=worker_id,
        )

    handler = _HANDLERS.get(ctx.kind)
    started = time.perf_counter()
    log_extra = {"job_id": str(ctx.job_id), "kind": ctx.kind, "attempt": ctx.attempt}
    try:
        if handler is None:
            raise PermanentJobError(f"no handler registered for job kind '{ctx.kind}'")
        with tracer.start_as_current_span(
            f"job {ctx.kind}",
            attributes={
                "nimbrix.job.kind": ctx.kind,
                "nimbrix.job.id": str(ctx.job_id),
                "nimbrix.job.attempt": ctx.attempt,
            },
        ):
            result = handler(ctx)
    except (RetryableJobError, OperationalError) as exc:
        with session_factory() as session:
            state = queue.fail(session, ctx.job_id, f"{type(exc).__name__}: {exc}", retryable=True)
        outcome = "retry" if state == "queued" else "failed"
        logger.warning("job_failed", extra={**log_extra, "outcome": outcome, "error": str(exc)})
    except Exception as exc:
        with session_factory() as session:
            queue.fail(session, ctx.job_id, f"{type(exc).__name__}: {exc}", retryable=False)
        outcome = "failed"
        logger.exception("job_failed", extra={**log_extra, "outcome": outcome})
    else:
        with session_factory() as session:
            queue.complete(session, ctx.job_id, result)
        outcome = "succeeded"
        logger.info("job_succeeded", extra=log_extra)
    JOBS_PROCESSED.labels(kind=ctx.kind, outcome=outcome).inc()
    JOB_DURATION.labels(kind=ctx.kind).observe(time.perf_counter() - started)
    return True


def drain(
    session_factory: sessionmaker[Session],
    settings: Settings,
    *,
    max_jobs: int = 100,
    worker_id: str = "inline",
) -> int:
    """Run runnable jobs synchronously until none remain (tests and CLI)."""
    processed = 0
    while processed < max_jobs and run_one(session_factory, settings, worker_id):
        processed += 1
    return processed


def run_forever(session_factory: sessionmaker[Session], settings: Settings) -> None:
    worker_id = default_worker_id()
    stop = threading.Event()

    def _stop(signum: int, _frame: object) -> None:
        logger.info("worker_stopping", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    start_http_server(settings.worker_metrics_port, registry=WORKER_REGISTRY)
    logger.info("worker_started", extra={"worker_id": worker_id})
    from nimbrix_api.modules.analysis.scheduler import schedule_daily_analysis  # noqa: PLC0415
    from nimbrix_api.modules.maintenance import run_retention  # noqa: PLC0415

    next_schedule = 0.0
    next_retention = 0.0
    while not stop.is_set():
        if time.monotonic() >= next_schedule:
            try:
                with session_factory() as session:
                    scheduled = schedule_daily_analysis(session)
                if scheduled:
                    logger.info("analysis_scheduled", extra={"tenants": scheduled})
                if time.monotonic() >= next_retention:
                    with session_factory() as session:
                        purged = run_retention(session, settings)
                    logger.info("retention_applied", extra=purged)
                    next_retention = time.monotonic() + RETENTION_INTERVAL_SECONDS
            except OperationalError:
                logger.exception("scheduler_database_error")
            next_schedule = time.monotonic() + SCHEDULE_INTERVAL_SECONDS
        try:
            worked = run_one(session_factory, settings, worker_id)
        except OperationalError:
            logger.exception("worker_database_error")
            worked = False
        if not worked:
            stop.wait(settings.worker_poll_interval_seconds)
