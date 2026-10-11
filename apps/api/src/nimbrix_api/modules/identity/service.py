"""Demo authentication: password check, session issue/revoke."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from nimbrix_api.core.audit import record_audit
from nimbrix_api.core.business_metrics import AUTH_EVENTS
from nimbrix_api.core.config import Settings
from nimbrix_api.core.errors import ApiError
from nimbrix_api.core.security import (
    DUMMY_PASSWORD_HASH,
    new_token,
    sha256_hex,
    verify_password,
)
from nimbrix_api.models import Membership, User
from nimbrix_api.models import Session as SessionRow


@dataclass(frozen=True)
class IssuedSession:
    token: str
    csrf_token: str
    row: SessionRow
    membership: Membership


def login(
    session: Session,
    settings: Settings,
    *,
    email: str,
    password: str,
    tenant_slug: str | None,
    user_agent: str | None,
) -> IssuedSession:
    if not settings.demo_auth_enabled:
        raise ApiError(403, "demo_auth_disabled", "Password login is disabled")
    email = email.strip().lower()
    user = session.scalar(
        select(User)
        .options(joinedload(User.memberships).joinedload(Membership.tenant))
        .where(User.email == email)
    )
    # Always run the KDF so response timing does not reveal whether the user exists.
    valid = verify_password(
        password, user.password_hash if user and user.password_hash else DUMMY_PASSWORD_HASH
    )
    if user is None or not user.is_active or not valid or not user.password_hash:
        record_audit(
            session,
            event_type="auth.login_failed",
            tenant_id=None,
            actor_type="anonymous",
            actor_label=email[:320],
            outcome="denied",
        )
        session.commit()
        AUTH_EVENTS.labels(outcome="failure").inc()
        raise ApiError(401, "invalid_credentials", "Invalid email or password")

    memberships = sorted(user.memberships, key=lambda m: m.tenant.slug)
    if tenant_slug:
        memberships = [m for m in memberships if m.tenant.slug == tenant_slug]
    if not memberships:
        raise ApiError(403, "no_tenant_access", "User has no access to the requested tenant")
    membership = memberships[0]

    token, csrf = new_token(), new_token()
    row = SessionRow(
        token_hash=sha256_hex(token),
        csrf_token_hash=sha256_hex(csrf),
        user_id=user.id,
        tenant_id=membership.tenant_id,
        expires_at=datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours),
        user_agent=(user_agent or "")[:256] or None,
    )
    session.add(row)
    record_audit(
        session,
        event_type="auth.login_succeeded",
        tenant_id=membership.tenant_id,
        actor_id=user.id,
        actor_label=user.email,
        target_type="session",
        payload={"role": membership.role.name},
    )
    session.flush()
    AUTH_EVENTS.labels(outcome="success").inc()
    return IssuedSession(token=token, csrf_token=csrf, row=row, membership=membership)


def logout(session: Session, session_id: object) -> None:
    row = session.get(SessionRow, session_id)
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
