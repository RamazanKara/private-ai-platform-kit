"""Optional OpenTelemetry tracing with OTLP/HTTP span export.

Tracing is opt-in. When ``OTEL_TRACING_ENABLED`` is false (the default) ``configure_tracing``
returns ``None`` and the request path adds no tracing overhead. When enabled, a SERVER span
is created per request, linked to any inbound W3C ``traceparent``, and exported over OTLP/HTTP
to ``OTEL_EXPORTER_OTLP_ENDPOINT``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

    from app.settings import Settings


def build_tracer_provider(settings: Settings, exporter: Any | None = None) -> Any:
    """Build a TracerProvider for the service; the span exporter is overridable for tests."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    if exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
    provider = TracerProvider(resource=Resource.create({"service.name": settings.otel_service_name}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def configure_tracing(settings: Settings, exporter: Any | None = None) -> tuple[Any, Any] | None:
    """Return ``(tracer, provider)`` when tracing is enabled, otherwise ``None``."""
    if not settings.otel_tracing_enabled:
        return None
    provider = build_tracer_provider(settings, exporter)
    return provider.get_tracer(settings.otel_service_name), provider


async def trace_request(
    tracer: Any,
    request: Request,
    dispatch: Callable[[], Awaitable[Response]],
) -> Response:
    """Run ``dispatch`` inside a SERVER span linked to the request's inbound trace context."""
    from opentelemetry.propagate import extract
    from opentelemetry.trace import SpanKind

    context = extract(dict(request.headers))
    with tracer.start_as_current_span(
        f"{request.method} {request.url.path}",
        context=context,
        kind=SpanKind.SERVER,
    ) as span:
        span.set_attribute("http.request.method", request.method)
        span.set_attribute("url.path", request.url.path)
        response = await dispatch()
        span.set_attribute("http.response.status_code", response.status_code)
        return response
