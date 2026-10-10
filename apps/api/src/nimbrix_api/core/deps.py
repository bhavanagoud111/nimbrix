"""Request-scoped dependencies: database session, authentication, authorization."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from nimbrix_api.core.audit import record_audit
from nimbrix_api.core.config import Settings, get_settings
from nimbrix_api.core.errors import ApiError
from nimbrix_api.core.permissions import Permission, permissions_for
from nimbrix_api.core.security import sha256_hex, tokens_match
from nimbrix_api.models import Membership, Tenant, User
from nimbrix_api.models import Session as SessionRow

SESSION_COOKIE = "nimbrix_session"
CSRF_COOKIE = "nimbrix_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Refresh last_seen_at at most this often to avoid a write per request.
_LAST_SEEN_RESOLUTION = timedelta(minutes=5)


def get_session(request: Request) -> Iterator[Session]:
    """A database session per request. Services commit explicitly; anything else rolls back."""
    database = request.app.state.database
    with database.session_factory() as session:
        try:
            yield session
        finally:
            session.rollback()


DbSession = Annotated[Session, Depends(get_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]


@dataclass(frozen=True)
class AuthContext:
    """Who is calling, for which tenant, with which permissions. Derived only from the session."""

    user_id: uuid.UUID
    email: str
    display_name: str
    tenant_id: uuid.UUID
    tenant_slug: str
    tenant_name: str
    role: str
    permissions: frozenset[Permission]
    session_id: uuid.UUID
    session_expires_at: datetime
    auth_method: str

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions


def _extract_token(request: Request) -> tuple[str | None, str]:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None, "bearer"
    return request.cookies.get(SESSION_COOKIE), "cookie"


def get_auth_context(request: Request, session: DbSession) -> AuthContext:
    token, transport = _extract_token(request)
    if not token:
        raise ApiError(401, "unauthenticated", "Authentication required")

    now = datetime.now(UTC)
    row = session.execute(
        select(SessionRow, User, Tenant, Membership)
        .join(User, User.id == SessionRow.user_id)
        .join(Tenant, Tenant.id == SessionRow.tenant_id)
        .join(
            Membership,
            (Membership.user_id == SessionRow.user_id)
            & (Membership.tenant_id == SessionRow.tenant_id),
        )
        .where(SessionRow.token_hash == sha256_hex(token))
    ).one_or_none()
    if row is None:
        raise ApiError(401, "unauthenticated", "Session is invalid or expired")
    db_session, user, tenant, membership = row
    if db_session.revoked_at is not None or db_session.expires_at <= now or not user.is_active:
        raise ApiError(401, "unauthenticated", "Session is invalid or expired")

    # CSRF: cookie-authenticated mutations must echo the CSRF token (double submit,
    # validated against the server-side hash bound to this session).
    if transport == "cookie" and request.method not in SAFE_METHODS:
        presented = request.headers.get(CSRF_HEADER)
        if not presented or not tokens_match(presented, db_session.csrf_token_hash):
            raise ApiError(403, "csrf_failed", "Missing or invalid CSRF token")

    if db_session.last_seen_at is None or now - db_session.last_seen_at > _LAST_SEEN_RESOLUTION:
        db_session.last_seen_at = now
        session.commit()

    role = membership.role.name
    return AuthContext(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        tenant_name=tenant.name,
        role=role,
        permissions=permissions_for(role),
        session_id=db_session.id,
        session_expires_at=db_session.expires_at,
        auth_method=f"demo-session:{transport}",
    )


CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]


def require(permission: Permission) -> Callable[..., AuthContext]:
    """Dependency factory enforcing a permission; denials are audited."""

    def dependency(auth: CurrentAuth, session: DbSession, request: Request) -> AuthContext:
        if not auth.can(permission):
            record_audit(
                session,
                event_type="authz.denied",
                tenant_id=auth.tenant_id,
                actor_id=auth.user_id,
                actor_label=auth.email,
                target_type="endpoint",
                target_id=f"{request.method} {request.url.path}",
                outcome="denied",
                payload={"required_permission": permission.value, "role": auth.role},
            )
            session.commit()
            raise ApiError(403, "forbidden", f"Role '{auth.role}' lacks permission '{permission}'")
        return auth

    return dependency


def idempotency_key(
    key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description="Unique key per logical operation; retries with the same key are safe.",
        ),
    ] = None,
) -> str:
    if key is None or not (8 <= len(key) <= 128) or not key.isascii() or not key.isprintable():
        raise ApiError(
            400,
            "idempotency_key_required",
            "Mutations require an Idempotency-Key header of 8-128 printable ASCII characters",
        )
    return key


IdempotencyKey = Annotated[str, Depends(idempotency_key)]
