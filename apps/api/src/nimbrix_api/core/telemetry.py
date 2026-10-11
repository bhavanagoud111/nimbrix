"""OpenTelemetry tracing (opt-in via NIMBRIX_OTEL_ENABLED).

Spans carry identifiers (route templates, job kinds, UUIDs) but never request bodies,
headers, SQL parameters, billing amounts, or model prompts. When tracing is off, the
OpenTelemetry API is a no-op and ``tracer`` spans cost almost nothing.
"""

from __future__ import annotations

import logging
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from nimbrix_api import __version__
from nimbrix_api.core.config import Settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("nimbrix")
_configured = False
EXCLUDED_URLS = "api/v1/health/.*,api/v1/metrics"


def setup_tracing(
    settings: Settings,
    *,
    app: Any = None,
    engine: Any = None,
    processor: SpanProcessor | None = None,
) -> bool:
    """Install a tracer provider once per process; instrument the app/engine if given."""
    global _configured  # noqa: PLW0603 - process-wide singleton, like the OTel provider itself
    if not settings.otel_enabled and processor is None:
        return False
    if not _configured:
        provider = TracerProvider(
            resource=Resource.create(
                {"service.name": settings.otel_service_name, "service.version": __version__}
            )
        )
        if processor is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # noqa: PLC0415

            endpoint = (settings.otel_exporter_otlp_endpoint or "").rstrip("/")
            processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)
        _configured = True
        logger.info("tracing_enabled", extra={"service": settings.otel_service_name})
    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: PLC0415

        FastAPIInstrumentor.instrument_app(
            app, excluded_urls=EXCLUDED_URLS, exclude_spans=["receive", "send"]
        )
    if engine is not None:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor  # noqa: PLC0415

        # Statements are recorded without parameter values; comments are disabled.
        SQLAlchemyInstrumentor().instrument(engine=engine, enable_commenter=False)
    return True


def current_trace_ids() -> tuple[str, str] | None:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return None
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
