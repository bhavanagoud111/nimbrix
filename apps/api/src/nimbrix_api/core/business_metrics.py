"""Domain metrics. Counters are incremented where events happen; gauges are read from the
database at scrape time. Labels are low-cardinality enums only (no tenant names, no amounts)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from prometheus_client import Counter, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from sqlalchemy import func, select

from nimbrix_api.core.metrics import REGISTRY
from nimbrix_api.modules.jobs.runner import WORKER_REGISTRY

IMPORT_LATENCY = Histogram(
    "nimbrix_import_duration_seconds",
    "Billing import processing time",
    labelnames=("source", "state"),
    registry=WORKER_REGISTRY,
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300),
)
IMPORT_ROWS = Counter(
    "nimbrix_import_rows_total",
    "Rows processed by imports",
    labelnames=("source", "result"),
    registry=WORKER_REGISTRY,
)
AGENT_CALLS = Counter(
    "nimbrix_agent_model_calls_total",
    "Investigation model calls",
    labelnames=("provider", "outcome"),
    registry=WORKER_REGISTRY,
)
AGENT_TOKENS = Counter(
    "nimbrix_agent_tokens_total",
    "LLM tokens used by investigations",
    labelnames=("provider", "direction"),
    registry=WORKER_REGISTRY,
)
INVESTIGATIONS = Counter(
    "nimbrix_investigations_total",
    "Finished investigations",
    labelnames=("state", "mode"),
    registry=WORKER_REGISTRY,
)
REMEDIATIONS = Counter(
    "nimbrix_remediation_executions_total",
    "Remediation execution outcomes",
    labelnames=("action_type", "mode", "state"),
    registry=WORKER_REGISTRY,
)
APPROVAL_DECISIONS = Counter(
    "nimbrix_approval_decisions_total",
    "Approval decisions",
    labelnames=("decision",),
    registry=REGISTRY,
)
POLICY_DENIALS = Counter(
    "nimbrix_policy_denials_total",
    "Requests denied by remediation policy",
    labelnames=("stage",),
    registry=REGISTRY,
)
AUTH_EVENTS = Counter(
    "nimbrix_auth_events_total",
    "Authentication outcomes",
    labelnames=("outcome",),
    registry=REGISTRY,
)
RATE_LIMITED = Counter(
    "nimbrix_rate_limited_total",
    "Requests rejected by rate limits",
    labelnames=("bucket",),
    registry=REGISTRY,
)


class DatabaseGaugeCollector(Collector):
    """Recommendation and job gauges computed with two cheap GROUP BY queries per scrape."""

    def __init__(self, session_factory: Any) -> None:
        self.session_factory = session_factory

    def collect(self) -> Iterator[GaugeMetricFamily]:
        from nimbrix_api.models import Job, Recommendation  # noqa: PLC0415 - avoid import cycles at startup

        recs = GaugeMetricFamily(
            "nimbrix_recommendations",
            "Recommendations by status and category",
            labels=["status", "category"],
        )
        jobs = GaugeMetricFamily("nimbrix_jobs", "Jobs by kind and state", labels=["kind", "state"])
        try:
            with self.session_factory() as session:
                for status, category, count in session.execute(
                    select(Recommendation.status, Recommendation.category, func.count()).group_by(
                        Recommendation.status, Recommendation.category
                    )
                ):
                    recs.add_metric([status, category], count)
                for kind, state, count in session.execute(
                    select(Job.kind, Job.state, func.count()).group_by(Job.kind, Job.state)
                ):
                    jobs.add_metric([kind, state], count)
        except Exception:
            return
        yield recs
        yield jobs
