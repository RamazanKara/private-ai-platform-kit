import asyncio
import time

import httpx
import pytest
from app import runtime_client
from app.runtime_client import RuntimeClient
from app.settings import Settings


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


def _mock_async_client(monkeypatch, handler):
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        runtime_client.httpx,
        "AsyncClient",
        lambda *args, **kwargs: real_async_client(transport=httpx.MockTransport(handler)),
    )


def test_stream_chat_completions_passes_through_runtime_chunks(monkeypatch):
    body = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, content=body)

    _mock_async_client(monkeypatch, handler)
    client = RuntimeClient(_settings())

    async def collect():
        return [chunk async for chunk in client.stream_chat_completions({"messages": []}, backend="ollama")]

    chunks = asyncio.run(collect())

    assert b"".join(chunks) == body
    assert seen["url"].endswith("/v1/chat/completions")
    # A successful stream resets the circuit breaker for the backend.
    assert client._failures.get("ollama", 0) == 0


def test_stream_chat_completions_raises_when_circuit_open():
    client = RuntimeClient(_settings())
    client._opened_until["ollama"] = time.time() + 60

    async def drain():
        async for _ in client.stream_chat_completions({"messages": []}, backend="ollama"):
            pass

    with pytest.raises(httpx.ConnectError):
        asyncio.run(drain())


def test_health_falls_back_to_health_endpoint(monkeypatch):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/healthz":
            return httpx.Response(404)
        return httpx.Response(200, json={"status": "serving"})

    _mock_async_client(monkeypatch, handler)

    result = asyncio.run(RuntimeClient(_settings()).health("ollama"))

    assert result == {"status": "serving"}
    assert paths == ["/healthz", "/health"]


def test_health_defaults_status_when_body_is_not_json(monkeypatch):
    def handler(request):
        return httpx.Response(200, content=b"OK")

    _mock_async_client(monkeypatch, handler)

    assert asyncio.run(RuntimeClient(_settings()).health("ollama")) == {"status": "ok"}


def test_health_raises_when_circuit_open():
    client = RuntimeClient(_settings())
    client._opened_until["vllm"] = time.time() + 60

    with pytest.raises(httpx.ConnectError):
        asyncio.run(client.health("vllm"))


def test_record_failure_is_noop_when_threshold_disabled():
    client = RuntimeClient(_settings(runtime_circuit_failure_threshold=0))

    for _ in range(5):
        client._record_failure("ollama")

    # With the breaker disabled, failures never latch the circuit open.
    assert "ollama" not in client._opened_until
