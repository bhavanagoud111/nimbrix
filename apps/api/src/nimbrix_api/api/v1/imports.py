"""Billing imports: synthetic demo data and CSV uploads (processed asynchronously)."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from nimbrix_api.api.v1.paths import API_V1_PREFIX
from nimbrix_api.core.audit import record_audit
from nimbrix_api.core.deps import AppSettings, AuthContext, DbSession, IdempotencyKey, require
from nimbrix_api.core.errors import ApiError
from nimbrix_api.core.idempotency import IdempotencyGuard
from nimbrix_api.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, paginate
from nimbrix_api.core.permissions import Permission
from nimbrix_api.core.tenancy import get_owned
from nimbrix_api.models import BillingImport, ImportFile, Job
from nimbrix_api.modules.ingestion import service
from nimbrix_api.modules.ingestion.requests import request_demo_import
from nimbrix_api.modules.ingestion.schemas import DemoImportRequest, ImportList, ImportOut
from nimbrix_api.modules.jobs import queue

router = APIRouter(prefix=API_V1_PREFIX, tags=["imports"])
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_CHUNK = 64 * 1024


def import_out(session: Session, row: BillingImport) -> ImportOut:
    job_id = session.scalar(
        select(Job.id)
        .where(Job.tenant_id == row.tenant_id, Job.payload["import_id"].astext == str(row.id))
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    return ImportOut(
        id=row.id,
        source=row.source,
        state=row.state,
        mode="demo" if row.source == "demo" else "live",
        checksum=row.checksum,
        filename=row.filename,
        period_start=row.period_start,
        period_end=row.period_end,
        rows_received=row.rows_received,
        rows_inserted=row.rows_inserted,
        rows_updated=row.rows_updated,
        rows_unchanged=row.rows_unchanged,
        rows_rejected=row.rows_rejected,
        resources_upserted=row.resources_upserted,
        errors=list(row.errors),
        details=dict(row.details),
        duplicate_of=row.duplicate_of,
        job_id=job_id,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _duplicate(
    session: Session, auth: AuthContext, source: str, checksum: str, filename: str | None
) -> BillingImport | None:
    original = service.find_completed_duplicate(session, auth.tenant_id, source, checksum)
    if original is None:
        return None
    duplicate = BillingImport(
        tenant_id=auth.tenant_id,
        source=source,
        state="duplicate",
        checksum=checksum,
        filename=filename,
        duplicate_of=original.id,
        requested_by=auth.user_id,
        period_start=original.period_start,
        period_end=original.period_end,
        errors=[],
        details={"message": "identical data was already imported; nothing to do"},
        finished_at=datetime.now(UTC),
    )
    session.add(duplicate)
    session.flush()
    return duplicate


def _audit_request(session: Session, auth: AuthContext, row: BillingImport) -> None:
    record_audit(
        session,
        event_type="import.requested",
        tenant_id=auth.tenant_id,
        actor_id=auth.user_id,
        actor_label=auth.email,
        target_type="billing_import",
        target_id=row.id,
        payload={"source": row.source, "state": row.state, "filename": row.filename},
    )


@router.post(
    "/imports/demo",
    response_model=ImportOut,
    status_code=202,
    summary="Import reproducible synthetic demo data (labeled demo, never live)",
)
def import_demo(
    request: Request,
    key: IdempotencyKey,
    auth: Annotated[AuthContext, Depends(require(Permission.IMPORTS_WRITE))],
    session: DbSession,
    body: DemoImportRequest | None = None,
) -> ImportOut | JSONResponse:
    body = body or DemoImportRequest()
    guard = IdempotencyGuard(
        session, auth, key, "POST", request.url.path, body.model_dump(mode="json")
    )
    if (replayed := guard.replay()) is not None:
        return replayed
    end_date = body.end_date or (datetime.now(UTC).date() - timedelta(days=1))
    requested = request_demo_import(
        session,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        end_date=end_date,
        days=body.days,
        seed=body.seed,
    )
    row = requested.billing_import
    _audit_request(session, auth, row)
    out = import_out(session, row)
    status = 200 if requested.duplicate else 202
    guard.remember(status, out)
    session.commit()
    if requested.duplicate:
        return JSONResponse(status_code=200, content=out.model_dump(mode="json"))
    return out


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := await upload.read(_CHUNK):
        size += len(chunk)
        if size > limit:
            raise ApiError(413, "upload_too_large", f"Uploads are limited to {limit:,} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/imports/upload",
    response_model=ImportOut,
    status_code=202,
    summary="Upload a billing CSV (validated per row by a background job)",
)
async def import_upload(
    request: Request,
    key: IdempotencyKey,
    auth: Annotated[AuthContext, Depends(require(Permission.IMPORTS_WRITE))],
    session: DbSession,
    settings: AppSettings,
    file: Annotated[UploadFile, File(description="CSV file, UTF-8")],
) -> ImportOut | JSONResponse:
    content = await _read_limited(file, settings.upload_max_bytes)
    if not content.strip():
        raise ApiError(422, "empty_file", "The uploaded file is empty")
    sha256 = hashlib.sha256(content).hexdigest()
    filename = _SAFE_FILENAME.sub("_", (file.filename or "upload.csv").rsplit("/", 1)[-1])[:255]
    guard = IdempotencyGuard(session, auth, key, "POST", request.url.path, {"sha256": sha256})
    if (replayed := guard.replay()) is not None:
        return replayed

    duplicate = _duplicate(session, auth, "csv", sha256, filename)
    if duplicate is not None:
        _audit_request(session, auth, duplicate)
        out = import_out(session, duplicate)
        guard.remember(200, out)
        session.commit()
        return JSONResponse(status_code=200, content=out.model_dump(mode="json"))

    row = BillingImport(
        tenant_id=auth.tenant_id,
        source="csv",
        state="queued",
        checksum=sha256,
        filename=filename,
        requested_by=auth.user_id,
        errors=[],
        details={"size_bytes": len(content)},
    )
    session.add(row)
    session.flush()
    session.add(
        ImportFile(
            tenant_id=auth.tenant_id,
            import_id=row.id,
            filename=filename,
            content_type=(file.content_type or "")[:128] or None,
            size_bytes=len(content),
            sha256=sha256,
            content=content,
        )
    )
    queue.enqueue(
        session,
        tenant_id=auth.tenant_id,
        kind="import_csv",
        payload={"import_id": str(row.id)},
        created_by=auth.user_id,
        dedupe_key=f"import:{row.id}",
    )
    _audit_request(session, auth, row)
    out = import_out(session, row)
    guard.remember(202, out)
    session.commit()
    return out


@router.get("/imports", response_model=ImportList, summary="List billing imports")
def list_imports(
    auth: Annotated[AuthContext, Depends(require(Permission.READ))],
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> ImportList:
    stmt = select(BillingImport).where(BillingImport.tenant_id == auth.tenant_id)
    rows, next_cursor = paginate(
        session, stmt, BillingImport.created_at, BillingImport.id, limit, cursor
    )
    return ImportList(items=[import_out(session, r) for r in rows], next_cursor=next_cursor)


@router.get(
    "/imports/{import_id}", response_model=ImportOut, summary="Import status and row errors"
)
def get_import(
    import_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(require(Permission.READ))],
    session: DbSession,
) -> ImportOut:
    return import_out(
        session, get_owned(session, BillingImport, auth, import_id, label="billing_import")
    )
