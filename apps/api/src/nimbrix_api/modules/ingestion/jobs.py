"""Ingestion job handlers: demo fixtures, CSV uploads, AWS Cost Explorer/CUR, EC2 inventory."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nimbrix_api.core.audit import record_audit
from nimbrix_api.models import BillingImport, CloudConnection, ImportFile
from nimbrix_api.modules.ingestion import aws, service, synthetic
from nimbrix_api.modules.ingestion.csv_source import CsvFormatError, parse_csv
from nimbrix_api.modules.ingestion.records import CostRecord, RowError
from nimbrix_api.modules.jobs.queue import PermanentJobError, RetryableJobError
from nimbrix_api.modules.jobs.runner import JobContext, register, registered_kinds

ANALYZE_JOB = "analyze_tenant"


# Injectable for tests (botocore Stubber-backed sessions); production builds sessions per job.
def _default_aws_session_factory(ctx: JobContext) -> aws.AwsSessionFactory:
    return aws.AwsSessionFactory(ctx.settings)


aws_session_factory: Callable[[JobContext], aws.AwsSessionFactory] = _default_aws_session_factory


def _load_import(session: Session, ctx: JobContext) -> BillingImport:
    import_id = uuid.UUID(ctx.payload["import_id"])
    row = session.scalar(
        select(BillingImport)
        .where(BillingImport.id == import_id, BillingImport.tenant_id == ctx.tenant_id)
        .with_for_update()
    )
    if row is None:
        raise PermanentJobError("billing import not found for this tenant")
    return row


@contextmanager
def _import_lifecycle(ctx: JobContext) -> Iterator[tuple[Session, BillingImport]]:
    """Marks the import running; on failure records the error (final attempts only for retries)."""
    with ctx.session_factory() as session:
        billing_import = _load_import(session, ctx)
        billing_import.state = "running"
        billing_import.started_at = billing_import.started_at or datetime.now(UTC)
        session.commit()
        try:
            yield session, billing_import
        except (PermanentJobError, RetryableJobError, CsvFormatError) as exc:
            session.rollback()
            final = not isinstance(exc, RetryableJobError) or ctx.is_final_attempt
            failed = _load_import(session, ctx)
            if final:
                failed.state = "failed"
                failed.finished_at = datetime.now(UTC)
            failed.errors = [RowError(row=None, code="import_failed", message=str(exc)).as_dict()]
            if final:
                record_audit(
                    session,
                    event_type="import.failed",
                    tenant_id=ctx.tenant_id,
                    actor_type="system",
                    target_type="billing_import",
                    target_id=failed.id,
                    outcome="failure",
                    payload={"source": failed.source, "error": str(exc)},
                )
            session.commit()
            if isinstance(exc, CsvFormatError):
                raise PermanentJobError(str(exc)) from None
            raise


def _persist(
    ctx: JobContext,
    session: Session,
    billing_import: BillingImport,
    records: list[CostRecord],
    *,
    errors: list[RowError],
    rejected: int,
    received: int,
    details: dict[str, Any],
) -> service.UpsertCounts:
    counts = service.upsert_cost_records(
        session, ctx.tenant_id, billing_import.id, records, on_batch=ctx.heartbeat
    )
    if records:
        billing_import.period_start = min(r.usage_date for r in records)
        billing_import.period_end = max(r.usage_date for r in records)
    service.finish_import(
        session,
        billing_import,
        received=received,
        counts=counts,
        errors=errors,
        rejected=rejected,
        resources=details.pop("resources_upserted", 0),
        details=details,
    )
    return counts


def _result(billing_import: BillingImport) -> dict[str, Any]:
    return {
        "import_id": str(billing_import.id),
        "state": billing_import.state,
        "inserted": billing_import.rows_inserted,
        "updated": billing_import.rows_updated,
        "unchanged": billing_import.rows_unchanged,
        "rejected": billing_import.rows_rejected,
    }


def _queue_analysis(ctx: JobContext, billing_import: BillingImport) -> None:
    if (
        billing_import.state in ("succeeded", "partially_failed")
        and ANALYZE_JOB in registered_kinds()
    ):
        ctx.enqueue_followup(
            ANALYZE_JOB,
            {"reason": "import", "import_id": str(billing_import.id)},
            dedupe_key=f"analyze:{ctx.tenant_id}",
        )


@register("import_demo")
def import_demo(ctx: JobContext) -> dict[str, Any]:
    end_date = date.fromisoformat(ctx.payload["end_date"])
    dataset = synthetic.generate(
        end_date, days=int(ctx.payload["days"]), seed=int(ctx.payload["seed"])
    )
    with _import_lifecycle(ctx) as (session, billing_import):
        resources = service.upsert_resources(
            session, ctx.tenant_id, billing_import.id, dataset.resources, source="demo"
        )
        budgets = service.upsert_budgets(session, ctx.tenant_id, dataset.budgets)
        _persist(
            ctx,
            session,
            billing_import,
            dataset.cost_records,
            errors=[],
            rejected=0,
            received=len(dataset.cost_records),
            details={
                "generator": synthetic.GENERATOR_VERSION,
                "resources_upserted": resources,
                "budgets_created": budgets,
                "labeled_anomalies": [
                    {**a, "start": str(a["start"]), "end": str(a["end"])}
                    for a in dataset.labeled_anomalies
                ],
            },
        )
        session.commit()
        result = _result(billing_import)
    _queue_analysis(ctx, billing_import)
    return result


@register("import_csv")
def import_csv(ctx: JobContext) -> dict[str, Any]:
    with _import_lifecycle(ctx) as (session, billing_import):
        upload = session.scalar(
            select(ImportFile).where(
                ImportFile.import_id == billing_import.id, ImportFile.tenant_id == ctx.tenant_id
            )
        )
        if upload is None:
            raise PermanentJobError("uploaded file is missing")
        parsed = parse_csv(upload.content, source_label="csv")
        _persist(
            ctx,
            session,
            billing_import,
            parsed.records,
            errors=parsed.errors,
            rejected=parsed.rejected,
            received=parsed.rows_received,
            details={"currencies": sorted(parsed.currencies)},
        )
        session.commit()
        result = _result(billing_import)
    _queue_analysis(ctx, billing_import)
    return result


def _connection(session: Session, ctx: JobContext) -> CloudConnection:
    connection = session.scalar(
        select(CloudConnection).where(
            CloudConnection.id == uuid.UUID(ctx.payload["connection_id"]),
            CloudConnection.tenant_id == ctx.tenant_id,
        )
    )
    if connection is None:
        raise PermanentJobError("connection not found for this tenant")
    return connection


@register("import_aws_cost_explorer")
def import_aws_cost_explorer(ctx: JobContext) -> dict[str, Any]:
    with _import_lifecycle(ctx) as (session, billing_import):
        connection = _connection(session, ctx)
        boto_session = aws_session_factory(ctx).for_connection(connection)
        start = date.fromisoformat(ctx.payload["start"])
        end = date.fromisoformat(ctx.payload["end"])
        records = list(aws.fetch_cost_explorer(boto_session, start, end))
        estimated = sum(1 for r in records if r.provenance.get("estimated"))
        _persist(
            ctx,
            session,
            billing_import,
            records,
            errors=[],
            rejected=0,
            received=len(records),
            details={"estimated_records": estimated, "granularity": "DAILY"},
        )
        session.commit()
        result = _result(billing_import)
    _queue_analysis(ctx, billing_import)
    return result


@register("sync_aws_inventory")
def sync_aws_inventory(ctx: JobContext) -> dict[str, Any]:
    with ctx.session_factory() as session:
        connection = _connection(session, ctx)
        boto_session = aws_session_factory(ctx).for_connection(connection)
        total = 0
        for region in connection.regions or [ctx.settings.aws_default_region]:
            resources = aws.fetch_ec2_inventory(boto_session, region, connection.account_id)
            total += service.upsert_resources(session, ctx.tenant_id, None, resources, source="aws")
            ctx.heartbeat()
        record_audit(
            session,
            event_type="inventory.synced",
            tenant_id=ctx.tenant_id,
            actor_type="system",
            target_type="cloud_connection",
            target_id=connection.id,
            payload={"resources_upserted": total, "regions": list(connection.regions)},
        )
        session.commit()
    return {"resources_upserted": total}
