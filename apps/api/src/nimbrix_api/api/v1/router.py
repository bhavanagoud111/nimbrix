"""Aggregates every /api/v1 router."""

from fastapi import APIRouter

from nimbrix_api.api.v1 import (
    anomalies,
    audit,
    auth,
    budgets,
    connections,
    costs,
    forecasts,
    health,
    imports,
    investigations,
    jobs,
    recommendations,
    resources,
)
from nimbrix_api.core.errors import ERROR_RESPONSES

api_router = APIRouter(responses=ERROR_RESPONSES)
for module in (
    health,
    auth,
    connections,
    imports,
    jobs,
    resources,
    costs,
    budgets,
    recommendations,
    anomalies,
    forecasts,
    investigations,
    audit,
):
    api_router.include_router(module.router)
