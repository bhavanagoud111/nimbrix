"""Budgets with utilization and threshold alerts."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from nimbrix_api.api.v1.paths import API_V1_PREFIX
from nimbrix_api.core.audit import record_audit
from nimbrix_api.core.deps import AuthContext, DbSession, IdempotencyKey, require
from nimbrix_api.core.errors import ApiError
from nimbrix_api.core.idempotency import IdempotencyGuard
from nimbrix_api.core.permissions import Permission
from nimbrix_api.core.tenancy import get_owned
from nimbrix_api.models import Budget, BudgetAlert
from nimbrix_api.modules.budgets import service
from nimbrix_api.modules.budgets.schemas import (
    BudgetAlertList,
    BudgetAlertOut,
    BudgetCreate,
    BudgetList,
    BudgetOut,
)

router = APIRouter(prefix=API_V1_PREFIX, tags=["budgets"])
Reader = Annotated[AuthContext, Depends(require(Permission.READ))]


@router.get("/budgets", response_model=BudgetList, summary="Budgets with current-month utilization")
def list_budgets(auth: Reader, session: DbSession) -> BudgetList:
    budgets = session.scalars(
        select(Budget).where(Budget.tenant_id == auth.tenant_id).order_by(Budget.name)
    ).all()
    return BudgetList(items=[BudgetOut(**service.evaluate(session, b).as_dict()) for b in budgets])


@router.post(
    "/budgets", response_model=BudgetOut, status_code=201, summary="Create a monthly budget"
)
def create_budget(
    body: BudgetCreate,
    request: Request,
    key: IdempotencyKey,
    auth: Annotated[AuthContext, Depends(require(Permission.BUDGETS_WRITE))],
    session: DbSession,
) -> BudgetOut | JSONResponse:
    guard = IdempotencyGuard(
        session, auth, key, "POST", request.url.path, body.model_dump(mode="json")
    )
    if (replayed := guard.replay()) is not None:
        return replayed
    if body.scope_type != "total" and not body.scope_value:
        raise ApiError(
            422, "scope_value_required", "scope_value is required unless scope_type is 'total'"
        )
    budget = Budget(
        tenant_id=auth.tenant_id,
        name=body.name,
        scope_type=body.scope_type,
        scope_value=None if body.scope_type == "total" else body.scope_value,
        amount=body.amount,
        currency=body.currency,
        alert_thresholds=[float(t) for t in body.alert_thresholds],
        owner=body.owner,
    )
    session.add(budget)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ApiError(409, "budget_exists", "A budget with this name already exists") from None
    record_audit(
        session,
        event_type="budget.created",
        tenant_id=auth.tenant_id,
        actor_id=auth.user_id,
        actor_label=auth.email,
        target_type="budget",
        target_id=budget.id,
        payload=body.model_dump(mode="json"),
    )
    out = BudgetOut(**service.evaluate(session, budget).as_dict())
    guard.remember(201, out)
    session.commit()
    return out


def _alert_out(alert: BudgetAlert, name: str) -> BudgetAlertOut:
    return BudgetAlertOut(
        id=alert.id,
        budget_id=alert.budget_id,
        budget_name=name,
        period_start=alert.period_start,
        threshold=alert.threshold,
        kind=alert.kind,
        utilization=alert.utilization,
        amount=alert.amount,
        currency=alert.currency,
        status=alert.status,
        details=alert.details,
        created_at=alert.created_at,
    )


@router.get("/budgets/alerts", response_model=BudgetAlertList, summary="Threshold alerts")
def list_alerts(auth: Reader, session: DbSession) -> BudgetAlertList:
    rows = session.execute(
        select(BudgetAlert, Budget.name)
        .join(Budget, Budget.id == BudgetAlert.budget_id)
        .where(BudgetAlert.tenant_id == auth.tenant_id)
        .order_by(BudgetAlert.created_at.desc())
        .limit(200)
    ).all()
    return BudgetAlertList(items=[_alert_out(a, n) for a, n in rows])


@router.post(
    "/budgets/alerts/{alert_id}/acknowledge",
    response_model=BudgetAlertOut,
    summary="Acknowledge an alert",
)
def acknowledge_alert(
    alert_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(require(Permission.BUDGETS_WRITE))],
    session: DbSession,
) -> BudgetAlertOut:
    alert = get_owned(session, BudgetAlert, auth, alert_id, label="budget_alert", for_update=True)
    alert.status = "acknowledged"
    record_audit(
        session,
        event_type="budget.alert_acknowledged",
        tenant_id=auth.tenant_id,
        actor_id=auth.user_id,
        actor_label=auth.email,
        target_type="budget_alert",
        target_id=alert.id,
    )
    budget = session.get(Budget, alert.budget_id)
    session.commit()
    return _alert_out(alert, budget.name if budget else "")
