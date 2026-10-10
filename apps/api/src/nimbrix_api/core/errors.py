"""Stable error envelope shared by every endpoint.

Shape: ``{"error": {"code", "message", "request_id", "details"}}``. Validation
details never echo submitted values back to the caller.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nimbrix_api.core.request_context import get_request_id

logger = logging.getLogger(__name__)


class ErrorDetail(BaseModel):
    field: str | None = Field(default=None, description="Dotted path of the offending input")
    message: str


class ErrorBody(BaseModel):
    code: str = Field(description="Stable machine-readable error code")
    message: str
    request_id: str | None = None
    details: list[ErrorDetail] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorBody


class ApiError(Exception):
    """Raise from application code to return a typed error envelope."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[ErrorDetail] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorDetail] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code, message=message, request_id=get_request_id(), details=details or []
        )
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(), headers=headers)


def _code_for_status(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).name.lower()
    except ValueError:
        return "http_error"


async def _handle_api_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)  # noqa: S101 - narrowed by registration
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def _handle_http_exception(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
    return error_response(
        exc.status_code,
        _code_for_status(exc.status_code),
        message,
        headers=dict(exc.headers) if exc.headers else None,
    )


async def _handle_validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    details = [
        ErrorDetail(field=".".join(str(p) for p in err.get("loc", ())), message=str(err["msg"]))
        for err in exc.errors()
    ]
    return error_response(422, "validation_error", "Request validation failed", details)


class UnhandledErrorMiddleware:
    """Turns unexpected exceptions into the 500 envelope.

    Starlette's own handler runs outside every user middleware, which would drop
    the request ID header and context. This middleware sits inside them instead.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            logger.exception("unhandled_error")
            if response_started:
                raise
            response = error_response(500, "internal_error", "An unexpected error occurred")
            await response(scope, receive, send)


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    "4XX": {"model": ErrorResponse, "description": "Client error"},
    "5XX": {"model": ErrorResponse, "description": "Server error"},
}


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
