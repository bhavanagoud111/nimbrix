"""Login, logout, current user, and current tenant."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select

from nimbrix_api.api.v1.paths import API_V1_PREFIX
from nimbrix_api.core.audit import record_audit
from nimbrix_api.core.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    AppSettings,
    AuthContext,
    CurrentAuth,
    DbSession,
    require,
)
from nimbrix_api.core.permissions import Permission, permissions_for
from nimbrix_api.models import Tenant
from nimbrix_api.modules.identity import service
from nimbrix_api.modules.identity.schemas import (
    LoginRequest,
    LoginResponse,
    MeResponse,
    TenantOut,
    UserOut,
)

router = APIRouter(prefix=API_V1_PREFIX, tags=["identity"])


def _me(auth: AuthContext) -> MeResponse:
    return MeResponse(
        user=UserOut(id=auth.user_id, email=auth.email, display_name=auth.display_name),
        tenant=TenantOut(id=auth.tenant_id, slug=auth.tenant_slug, name=auth.tenant_name),
        role=auth.role,
        permissions=sorted(auth.permissions),
        auth_method=auth.auth_method,
        session_expires_at=auth.session_expires_at,
    )


@router.post("/auth/login", response_model=LoginResponse, summary="Demo password login")
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
) -> LoginResponse:
    issued = service.login(
        session,
        settings,
        email=body.email,
        password=body.password,
        tenant_slug=body.tenant_slug,
        user_agent=request.headers.get("user-agent"),
    )
    membership = issued.membership
    user = membership.user
    session.commit()

    max_age = int((issued.row.expires_at - datetime.now(UTC)).total_seconds())
    secure = settings.secure_cookies
    response.set_cookie(
        SESSION_COOKIE, issued.token, max_age=max_age, httponly=True, secure=secure, samesite="lax"
    )
    response.set_cookie(
        CSRF_COOKIE,
        issued.csrf_token,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="strict",
    )
    return LoginResponse(
        user=UserOut(id=user.id, email=user.email, display_name=user.display_name),
        tenant=TenantOut(
            id=membership.tenant.id, slug=membership.tenant.slug, name=membership.tenant.name
        ),
        role=membership.role.name,
        permissions=sorted(permissions_for(membership.role.name)),
        auth_method="demo-session:cookie",
        session_expires_at=issued.row.expires_at,
        csrf_token=issued.csrf_token,
    )


@router.post("/auth/logout", status_code=204, summary="Revoke the current session")
def logout(
    auth: CurrentAuth, session: DbSession, response: Response, settings: AppSettings
) -> Response:
    service.logout(session, auth.session_id)
    record_audit(
        session,
        event_type="auth.logout",
        tenant_id=auth.tenant_id,
        actor_id=auth.user_id,
        actor_label=auth.email,
        target_type="session",
        target_id=auth.session_id,
    )
    session.commit()
    result = Response(status_code=204)
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        result.delete_cookie(name, secure=settings.secure_cookies, samesite="lax")
    return result


@router.get("/me", response_model=MeResponse, summary="Current user, tenant, and permissions")
def me(auth: CurrentAuth) -> MeResponse:
    return _me(auth)


@router.get("/tenants/current", response_model=TenantOut, summary="Tenant bound to the session")
def current_tenant(
    auth: Annotated[AuthContext, Depends(require(Permission.READ))], session: DbSession
) -> TenantOut:
    tenant = session.scalar(select(Tenant).where(Tenant.id == auth.tenant_id))
    assert tenant is not None  # noqa: S101 - FK guarantees existence
    return TenantOut(id=tenant.id, slug=tenant.slug, name=tenant.name)
