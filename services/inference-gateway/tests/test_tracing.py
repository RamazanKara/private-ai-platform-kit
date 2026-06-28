import asyncio

import pytest
from app.settings import Settings
from app.tracing import build_tracer_provider, configure_tracing, trace_request
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind


def _settings(**overrides):
    base = {
        "runtime_backend": "ollama",
        "ollama_base_url": "http://ollama:11434",
        "vllm_base_url": "http://vllm:8000",
        "model_id": "default-model",
        "request_timeout_seconds": 5,
    }
    base.update(overrides)
    return Settings(**base)


class _FakeURL:
    path = "/v1/chat/completions"


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


def test_configure_tracing_returns_tracer_when_enabled():
    settings = _settings(otel_tracing_enabled=True, otel_exporter_otlp_endpoint="http://collector:4318")
    result = configure_tracing(settings, exporter=InMemorySpanExporter())
    assert result is not None
    tracer, provider = result
    assert tracer is not None
    assert provider is not None


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

    response = asyncio.run(trace_request(tracer, _FakeRequest(), dispatch))
    provider.force_flush()

    assert response.status_code == 200
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "POST /v1/chat/completions"
    assert span.kind == SpanKind.SERVER
    assert span.attributes["http.response.status_code"] == 200
    assert span.attributes["url.path"] == "/v1/chat/completions"
