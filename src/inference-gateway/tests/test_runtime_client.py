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


def test_health_falls_back_to_root_when_health_endpoints_missing(monkeypatch):
    # Ollama serves readiness at "/" and has neither /healthz nor /health; the
    # gateway must fall back to "/" so /readyz can confirm the backend.
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path in ("/healthz", "/health"):
            return httpx.Response(404)
        return httpx.Response(200, content=b"Ollama is running")

    _mock_async_client(monkeypatch, handler)

    result = asyncio.run(RuntimeClient(_settings()).health("ollama"))

    assert result == {"status": "ok"}
    assert paths == ["/healthz", "/health", "/"]


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


def test_stream_chat_completions_targets_vllm_backend(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, content=b'data: {"choices":[]}\n\n')

    _mock_async_client(monkeypatch, handler)
    client = RuntimeClient(_settings(runtime_backend="vllm"))

    async def collect():
        return [chunk async for chunk in client.stream_chat_completions({"messages": []}, backend="vllm")]

    assert asyncio.run(collect())
    assert "vllm:8000" in seen["url"]


def test_stream_chat_completions_passes_through_malformed_events(monkeypatch):
    # The runtime client streams bytes verbatim; malformed SSE must pass through, not crash.
    body = b"data: {not-valid-json\n\n:: broken event\n\ndata: [DONE]\n\n"

    def handler(request):
        return httpx.Response(200, content=body)

    _mock_async_client(monkeypatch, handler)
    client = RuntimeClient(_settings())

    async def collect():
        return b"".join([chunk async for chunk in client.stream_chat_completions({"messages": []}, backend="ollama")])

    assert asyncio.run(collect()) == body


def test_stream_chat_completions_can_be_cancelled_mid_stream(monkeypatch):
    def handler(request):
        return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')

    _mock_async_client(monkeypatch, handler)
    client = RuntimeClient(_settings())

    async def cancel_after_first_chunk():
        stream = client.stream_chat_completions({"messages": []}, backend="ollama")
        first = await stream.__anext__()
        # Consumer cancels early (e.g. client disconnect); aclose must unwind the runtime stream cleanly.
        await stream.aclose()
        return first

    assert asyncio.run(cancel_after_first_chunk())


class _BreaksMidStream(httpx.AsyncByteStream):
    """A response body that yields one SSE event and then drops the connection."""

    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        raise httpx.ReadError("upstream dropped mid-stream")


def test_stream_is_not_retried_after_the_first_chunk(monkeypatch):
    # Retrying here would append a second, different completion to the partial one the
    # client already holds. The failure must surface instead, after exactly one POST.
    posts = []

    def handler(request):
        posts.append(request.url.path)
        return httpx.Response(200, stream=_BreaksMidStream())

    _mock_async_client(monkeypatch, handler)
    client = RuntimeClient(_settings(runtime_max_retries=2, runtime_retry_backoff_seconds=0.001))
    received = []

    async def drain():
        async for chunk in client.stream_chat_completions({"messages": []}, backend="ollama"):
            received.append(chunk)

    with pytest.raises(httpx.ReadError):
        asyncio.run(drain())
    assert posts == ["/v1/chat/completions"]
    assert b"".join(received).count(b"partial") == 1


def test_stream_retries_connect_errors_before_the_first_chunk(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("not yet listening", request=request)
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    _mock_async_client(monkeypatch, handler)
    client = RuntimeClient(_settings(runtime_max_retries=2, runtime_retry_backoff_seconds=0.001))

    async def collect():
        return b"".join([chunk async for chunk in client.stream_chat_completions({"messages": []}, backend="ollama")])

    assert asyncio.run(collect()) == b"data: [DONE]\n\n"
    assert len(calls) == 2


def test_read_timeout_on_generation_is_not_retried(monkeypatch):
    # The runtime accepted the request and is still generating; a retry doubles GPU work.
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("generation too slow", request=request)

    _mock_async_client(monkeypatch, handler)
    client = RuntimeClient(_settings(runtime_max_retries=2, runtime_retry_backoff_seconds=0.001))

    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(client.chat_completions({"messages": []}, backend="ollama"))
    assert len(calls) == 1


def test_client_errors_do_not_open_the_circuit(monkeypatch):
    # One tenant's malformed payloads (400/422) must not trip the breaker for everyone.
    def handler(request):
        return httpx.Response(422, json={"error": "bad request"})

    _mock_async_client(monkeypatch, handler)
    client = RuntimeClient(_settings(runtime_circuit_failure_threshold=1))

    for _ in range(3):
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(client.chat_completions({"messages": []}, backend="ollama"))
    assert "ollama" not in client._opened_until


def test_server_errors_still_open_the_circuit(monkeypatch):
    def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    _mock_async_client(monkeypatch, handler)
    client = RuntimeClient(_settings(runtime_circuit_failure_threshold=1, runtime_max_retries=0))

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(client.chat_completions({"messages": []}, backend="ollama"))
    assert "ollama" in client._opened_until
