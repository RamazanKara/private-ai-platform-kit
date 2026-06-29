import asyncio
from pathlib import Path

import pytest
from app.settings import Settings
from app.tracing import build_tracer_provider, configure_tracing, trace_request
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind


def _settings(**overrides):
    base = {"document_dir": Path("/knowledge")}
    base.update(overrides)
    return Settings(**base)


class _FakeURL:
    path = "/v1/rag/query"


class _FakeRequest:
    def __init__(self) -> None:
        self.method = "POST"
        self.url = _FakeURL()
        self.headers: dict[str, str] = {}


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def test_configure_tracing_is_none_when_disabled():
    assert configure_tracing(_settings(otel_tracing_enabled=False)) is None


def test_settings_require_endpoint_when_tracing_enabled():
    with pytest.raises(ValueError, match="otel_exporter_otlp_endpoint"):
        _settings(otel_tracing_enabled=True)


def test_trace_request_exports_a_server_span():
    settings = _settings(
        otel_tracing_enabled=True,
        otel_exporter_otlp_endpoint="http://collector:4318",
    )
    exporter = InMemorySpanExporter()
    provider = build_tracer_provider(settings, exporter=exporter)
    tracer = provider.get_tracer("test")

    async def dispatch():
        return _FakeResponse(200)

    asyncio.run(trace_request(tracer, _FakeRequest(), dispatch))
    provider.force_flush()

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "POST /v1/rag/query"
    assert spans[0].kind == SpanKind.SERVER
