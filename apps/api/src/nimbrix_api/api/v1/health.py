"""Liveness, readiness, and metrics endpoints."""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import StrEnum
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from nimbrix_api import __version__
from nimbrix_api.api.v1.paths import API_V1_PREFIX
from nimbrix_api.core.config import Environment, RemediationMode, Settings, get_settings
from nimbrix_api.core.metrics import REGISTRY
from nimbrix_api.db.session import DatabaseStatus, check_database, expected_migration_head

logger = logging.getLogger(__name__)

router = APIRouter(prefix=API_V1_PREFIX, tags=["health"])

SERVICE_NAME = "nimbrix-api"


class CheckStatus(StrEnum):
    OK = "ok"
    FAIL = "fail"


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = SERVICE_NAME
    version: str = __version__


class DatabaseCheck(BaseModel):
    status: CheckStatus
    latency_ms: float | None = None
    detail: str | None = Field(default=None, description="Safe, non-sensitive failure summary")


class MigrationCheck(BaseModel):
    status: CheckStatus
    current_revision: str | None = None
    expected_revision: str | None = None


class ReadinessChecks(BaseModel):
    database: DatabaseCheck
    migrations: MigrationCheck


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    service: str = SERVICE_NAME
    version: str = __version__
    environment: Environment
    remediation_mode: RemediationMode = Field(
        description="Remediation execution mode; 'simulation_only' means no cloud writes"
    )
    checks: ReadinessChecks


DatabaseProbe = Callable[[], DatabaseStatus]


def get_database_probe(request: Request) -> DatabaseProbe:
    database = request.app.state.database
    return lambda: check_database(database.engine)


@router.get("/health/live", response_model=LivenessResponse, summary="Process liveness")
def live() -> LivenessResponse:
    return LivenessResponse()


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness: database reachable and migrations at head",
    responses={503: {"model": ReadinessResponse, "description": "Dependency not ready"}},
)
def ready(
    response: Response,
    probe: Annotated[DatabaseProbe, Depends(get_database_probe)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReadinessResponse:
    try:
        db_status = probe()
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("readiness_database_unreachable", extra={"error_type": type(exc).__name__})
        checks = ReadinessChecks(
            database=DatabaseCheck(status=CheckStatus.FAIL, detail="database unreachable"),
            migrations=MigrationCheck(
                status=CheckStatus.FAIL, expected_revision=expected_migration_head()
            ),
        )
    else:
        checks = ReadinessChecks(
            database=DatabaseCheck(status=CheckStatus.OK, latency_ms=db_status.latency_ms),
            migrations=MigrationCheck(
                status=CheckStatus.OK if db_status.migrations_current else CheckStatus.FAIL,
                current_revision=db_status.current_revision,
                expected_revision=db_status.expected_revision,
            ),
        )

    is_ready = (
        checks.database.status is CheckStatus.OK and checks.migrations.status is CheckStatus.OK
    )
    if not is_ready:
        response.status_code = 503
    return ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        environment=settings.environment,
        remediation_mode=settings.remediation_mode,
        checks=checks,
    )


@router.get(
    "/metrics",
    summary="Prometheus metrics",
    response_class=Response,
    responses={
        200: {"content": {CONTENT_TYPE_LATEST: {}}, "description": "Prometheus text format"}
    },
)
def metrics() -> Response:
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
