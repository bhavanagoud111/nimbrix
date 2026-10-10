"""Aggregates every /api/v1 router."""

from fastapi import APIRouter

from nimbrix_api.api.v1 import health
from nimbrix_api.core.errors import ERROR_RESPONSES

api_router = APIRouter(responses=ERROR_RESPONSES)
api_router.include_router(health.router)
