from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nimbrix_api.core.config import Settings
from nimbrix_api.models import (
    BillingImport,
    IdempotencyRecord,
    ImportFile,
    User,
)
from nimbrix_api.models import (
    Session as SessionRow,
)
from nimbrix_api.modules.maintenance import run_retention
from tests.integration.conftest import create_tenant


def test_retention_purges_only_expired_data(db: Session, app_settings: Settings) -> None:
    users = create_tenant(db, ("admin",))
    user_id = db.scalar(select(User.id).where(User.email == users.emails["admin"]))
    now = datetime.now(UTC)
    old, recent = now - timedelta(days=60), now - timedelta(hours=1)
    imp = BillingImport(
        tenant_id=users.tenant_id, source="csv", state="succeeded", errors=[], details={}
    )
    db.add(imp)
    db.flush()
    db.add_all(
        [
            ImportFile(
                tenant_id=users.tenant_id,
                import_id=imp.id,
                filename="a.csv",
                size_bytes=1,
                sha256="0" * 64,
                content=b"x",
                created_at=old,
            ),
            IdempotencyRecord(
                tenant_id=users.tenant_id,
                user_id=user_id,
                key=f"old-{uuid.uuid4().hex}",
                method="POST",
                path="/x",
                request_hash="0" * 64,
                status_code=201,
                response_body={},
                expires_at=now - timedelta(minutes=1),
            ),
            IdempotencyRecord(
                tenant_id=users.tenant_id,
                user_id=user_id,
                key=f"new-{uuid.uuid4().hex}",
                method="POST",
                path="/x",
                request_hash="0" * 64,
                status_code=201,
                response_body={},
                expires_at=now + timedelta(hours=1),
            ),
            SessionRow(
                token_hash=uuid.uuid4().hex * 2,
                csrf_token_hash="0" * 64,
                user_id=user_id,
                tenant_id=users.tenant_id,
                expires_at=old,
            ),
            SessionRow(
                token_hash=uuid.uuid4().hex * 2,
                csrf_token_hash="0" * 64,
                user_id=user_id,
                tenant_id=users.tenant_id,
                expires_at=now + timedelta(hours=1),
                revoked_at=recent,
            ),
        ]
    )
    db.commit()
    purged = run_retention(db, app_settings, now)
    assert purged["raw_uploads"] >= 1
    assert purged["idempotency_keys"] >= 1
    assert purged["sessions"] >= 1
    assert (
        db.scalar(
            select(func.count()).select_from(ImportFile).where(ImportFile.import_id == imp.id)
        )
        == 0
    )
    assert db.get(BillingImport, imp.id) is not None  # the import record and its metadata remain
    remaining = db.scalar(
        select(func.count()).select_from(SessionRow).where(SessionRow.tenant_id == users.tenant_id)
    )
    assert remaining == 1  # the recently revoked session is kept until its retention passes
