"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nimbrix_api import __version__
from nimbrix_api.api.v1.router import api_router
from nimbrix_api.core.config import Settings, get_settings
from nimbrix_api.core.errors import UnhandledErrorMiddleware, register_error_handlers
from nimbrix_api.core.logging import configure_logging
from nimbrix_api.core.metrics import MetricsMiddleware
from nimbrix_api.core.request_context import REQUEST_ID_HEADER, RequestIdMiddleware
from nimbrix_api.db.session import Database, build_engine

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level.value)
    db = database or Database(build_engine(settings))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "api_startup",
            extra={
                "environment": settings.environment.value,
                "remediation_mode": settings.remediation_mode.value,
            },
        )
        try:
            yield
        finally:
            db.dispose()

    app = FastAPI(
        title="Nimbrix API",
        version=__version__,
        description=(
            "Cloud cost intelligence and optimization API. Remediation runs in "
            "simulation-only mode; no cloud write actions are available."
        ),
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = db
    app.dependency_overrides[get_settings] = lambda: settings

    register_error_handlers(app)
    # Middleware added later wraps earlier ones: RequestId -> Metrics -> (CORS) -> errors.
    app.add_middleware(UnhandledErrorMiddleware)
    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "Idempotency-Key", REQUEST_ID_HEADER],
            expose_headers=[REQUEST_ID_HEADER],
        )
    app.add_middleware(MetricsMiddleware)
    # Added last so it wraps everything: every log line and response carries the ID.
    app.add_middleware(RequestIdMiddleware)
    app.include_router(api_router)
    return app
