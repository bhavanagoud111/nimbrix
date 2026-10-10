"""Aggregates every /api/v1 router."""

from fastapi import APIRouter

from nimbrix_api.api.v1 import audit, auth, connections, health, imports, jobs, resources
from nimbrix_api.core.errors import ERROR_RESPONSES

api_router = APIRouter(responses=ERROR_RESPONSES)
for module in (health, auth, connections, imports, jobs, resources, audit):
    api_router.include_router(module.router)
