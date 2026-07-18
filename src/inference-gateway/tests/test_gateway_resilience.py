import asyncio

import httpx
import pytest
from app.budget import RedisSandboxBudgetTracker
from app.main import create_app
from app.runtime_client import RuntimeClient
from app.settings import Settings
from fastapi.testclient import TestClient

from tests.gateway_support import (
    FakeRuntimeClient,
    _retry_settings,
    _tool_settings,
)


def test_runtime_client_retries_non_streaming_requests(monkeypatch):
    calls = {"count": 0}

    class FlakyAsyncClient:
        def __init__(self, *args, **kwargs):
            self.timeout = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json=None, headers=None):
            calls["count"] += 1
            if calls["count"] == 1:
                raise httpx.ConnectError("temporary runtime failure")
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", FlakyAsyncClient)
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
        runtime_max_retries=1,
        runtime_retry_backoff_seconds=0.001,
    )
    client = RuntimeClient(settings)

    response = asyncio.run(client.chat_completions({"messages": [{"role": "user", "content": "hello"}]}))

    assert response["choices"][0]["message"]["content"] == "ok"
    assert calls["count"] == 2


def test_runtime_client_retries_retryable_5xx_status(monkeypatch):
    # Regression: a 503 from a busy GPU runtime raised immediately and surfaced as a
    # 502; retryable statuses now re-fire while attempts remain.
    calls = {"count": 0}

    class FlakyStatusClient:
        def __init__(self, *args, **kwargs):
            pass

        async def post(self, url, json=None, headers=None):
            calls["count"] += 1
            request = httpx.Request("POST", url)
            if calls["count"] == 1:
                return httpx.Response(503, request=request, text="overloaded")
            return httpx.Response(
                200,
                request=request,
                json={"id": "x", "object": "chat.completion", "choices": [{"message": {"content": "ok"}}]},
            )

    monkeypatch.setattr(httpx, "AsyncClient", FlakyStatusClient)
    client = RuntimeClient(_retry_settings())

    response = asyncio.run(client.chat_completions({"messages": [{"role": "user", "content": "hi"}]}))

    assert response["choices"][0]["message"]["content"] == "ok"
    assert calls["count"] == 2


def test_runtime_client_does_not_retry_client_error(monkeypatch):
    calls = {"count": 0}

    class ClientErrorClient:
        def __init__(self, *args, **kwargs):
            pass

        async def post(self, url, json=None, headers=None):
            calls["count"] += 1
            request = httpx.Request("POST", url)
            return httpx.Response(400, request=request, text="bad request")

    monkeypatch.setattr(httpx, "AsyncClient", ClientErrorClient)
    client = RuntimeClient(_retry_settings())

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(client.chat_completions({"messages": [{"role": "user", "content": "hi"}]}))
    assert calls["count"] == 1


def test_runtime_client_streaming_retries_before_first_byte(monkeypatch):
    calls = {"count": 0}

    class _StreamCtx:
        def __init__(self, status_code, chunks):
            self.status_code = status_code
            self._chunks = chunks
            self.headers = {}
            self.request = httpx.Request("POST", "http://ollama:11434/v1/chat/completions")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "err",
                    request=self.request,
                    response=httpx.Response(self.status_code, request=self.request),
                )

        async def aread(self):
            return b""

        async def aiter_bytes(self):
            for chunk in self._chunks:
                yield chunk

    class FlakyStreamClient:
        def __init__(self, *args, **kwargs):
            pass

        def stream(self, method, url, json=None, headers=None):
            calls["count"] += 1
            if calls["count"] == 1:
                return _StreamCtx(503, [])
            return _StreamCtx(200, [b'data: {"choices":[]}\n\n', b"data: [DONE]\n\n"])

    monkeypatch.setattr(httpx, "AsyncClient", FlakyStreamClient)
    client = RuntimeClient(_retry_settings())

    async def consume():
        chunks = []
        async for chunk in client.stream_chat_completions({"messages": [{"role": "user", "content": "hi"}]}):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(consume())

    assert calls["count"] == 2
    assert chunks[0] == b'data: {"choices":[]}\n\n'


def test_retry_after_seconds_parses_numeric_header():
    request = httpx.Request("POST", "http://runtime/v1/chat/completions")
    with_header = httpx.Response(503, request=request, headers={"Retry-After": "7"})
    without_header = httpx.Response(503, request=request)
    assert RuntimeClient._retry_after_seconds(with_header) == 7.0
    assert RuntimeClient._retry_after_seconds(without_header) is None
    assert RuntimeClient._retry_after_seconds(None) is None


def test_runtime_client_opens_circuit_after_failures(monkeypatch):
    calls = {"count": 0}

    class FailingAsyncClient:
        def __init__(self, *args, **kwargs):
            self.timeout = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json=None, headers=None):
            calls["count"] += 1
            raise httpx.ConnectError("runtime unavailable")

    monkeypatch.setattr(httpx, "AsyncClient", FailingAsyncClient)
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
        runtime_circuit_failure_threshold=1,
        runtime_circuit_reset_seconds=30,
    )
    client = RuntimeClient(settings)

    with pytest.raises(httpx.ConnectError):
        asyncio.run(client.chat_completions({"messages": [{"role": "user", "content": "hello"}]}))
    with pytest.raises(httpx.ConnectError, match="circuit is open"):
        asyncio.run(client.chat_completions({"messages": [{"role": "user", "content": "hello"}]}))

    assert calls["count"] == 1


def test_runtime_http_error_returns_502():
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
    )
    request = httpx.Request("POST", "http://runtime/v1/chat/completions")
    runtime_response = httpx.Response(503, request=request, text="unavailable")
    app = create_app(settings)
    app.state.runtime_client = FakeRuntimeClient(
        error=httpx.HTTPStatusError("runtime unavailable", request=request, response=runtime_response)
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 502
    assert response.json()["detail"]["runtime_status"] == 503


def test_runtime_network_error_returns_sanitized_502():
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama.internal:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
    )
    request = httpx.Request("POST", "http://ollama.internal:11434/v1/chat/completions")
    app = create_app(settings)
    app.state.runtime_client = FakeRuntimeClient(
        error=httpx.ConnectError("connect failed to http://ollama.internal:11434", request=request)
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 502
    assert response.json()["detail"]["message"] == "runtime request failed"
    assert "ollama.internal" not in response.text


def test_runtime_invalid_response_returns_sanitized_502():
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
    )
    app = create_app(settings)
    app.state.runtime_client = FakeRuntimeClient(error=ValueError("invalid JSON body: customer secret snippet"))
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 502
    assert response.json()["detail"]["message"] == "runtime returned an invalid response"
    assert "customer secret snippet" not in response.text


# --- backend-outage and hardening regressions -------------------------------------------


class _BrokenRedis:
    """Redis stand-in whose every operation fails like an unreachable backend."""

    def incr(self, key):
        raise OSError("redis down")

    def ttl(self, key):
        raise OSError("redis down")

    def expire(self, key, window):
        raise OSError("redis down")

    def eval(self, *args, **kwargs):
        raise OSError("redis down")

    def hgetall(self, key):
        raise OSError("redis down")


class _CountingRedis:
    """Redis stand-in tracking INCR counters and EXPIRE calls per key."""

    def __init__(self):
        self.counts = {}
        self.expirations = {}

    def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def ttl(self, key):
        return self.expirations.get(key, -1)

    def expire(self, key, window):
        self.expirations[key] = window


def test_rate_limit_backend_outage_returns_503_not_500():
    # A Redis outage on the rate-limit path must degrade to a retryable 503 with
    # Retry-After (the budget tracker's contract), never an unhandled 500.
    from app.ratelimit import RedisRateLimiter

    settings = _tool_settings(
        rate_limit_enabled=True,
        rate_limit_requests_per_window=1,
        rate_limit_window_seconds=60,
    )
    app = create_app(settings)
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    app.state.rate_limiter = RedisRateLimiter(settings, client=_BrokenRedis())
    client = TestClient(app)

    response = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "rate_limit_backend_unavailable"
    assert response.headers["Retry-After"] == "5"


def test_rate_limit_fail_open_admits_when_backend_down():
    # Opt-in availability-over-enforcement: with RATE_LIMIT_FAIL_OPEN set, a Redis outage
    # on the rate-limit path admits the request (no 503) instead of failing closed, and
    # records the fail-open metric so the degraded window is visible.
    from app.ratelimit import RedisRateLimiter
    from prometheus_client import REGISTRY

    settings = _tool_settings(
        rate_limit_enabled=True,
        rate_limit_requests_per_window=1,
        rate_limit_window_seconds=60,
        rate_limit_fail_open=True,
    )
    app = create_app(settings)
    completion = {
        "id": "x",
        "object": "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": "hi"}}],
    }
    app.state.runtime_client = FakeRuntimeClient(response=completion)
    app.state.rate_limiter = RedisRateLimiter(settings, client=_BrokenRedis())
    client = TestClient(app)

    before = REGISTRY.get_sample_value("inference_gateway_rate_limit_fail_open_total", {"sandbox": "local-lab"}) or 0.0
    response = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 200
    after = REGISTRY.get_sample_value("inference_gateway_rate_limit_fail_open_total", {"sandbox": "local-lab"})
    assert after == before + 1


def test_rate_limit_fail_open_does_not_weaken_budget_fail_closed():
    # Budgets must stay fail-closed even when the rate limiter is configured to fail open:
    # a budget-backend outage is still a 503, never admitted, regardless of the flag.
    from app.ratelimit import RedisRateLimiter

    settings = _tool_settings(
        rate_limit_enabled=True,
        rate_limit_requests_per_window=1,
        rate_limit_window_seconds=60,
        rate_limit_fail_open=True,
        sandbox_budget_enabled=True,
        sandbox_budget_backend="redis",
        sandbox_request_budget=100,
    )
    app = create_app(settings)
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    # Rate limiter is healthy (allows), but the budget backend is down.
    app.state.rate_limiter = RedisRateLimiter(settings, client=_CountingRedis())
    app.state.budget_tracker = RedisSandboxBudgetTracker(settings, client=_BrokenRedis())
    client = TestClient(app)

    response = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "budget_backend_unavailable"


def test_redis_rate_limiter_rearms_lost_ttl():
    # A crash between INCR and EXPIRE leaves a counter with no expiry; the limiter
    # must re-arm the TTL instead of locking the sandbox out permanently.
    from app.ratelimit import RedisRateLimiter

    settings = _tool_settings(
        rate_limit_enabled=True,
        rate_limit_requests_per_window=5,
        rate_limit_window_seconds=60,
    )
    fake = _CountingRedis()
    limiter = RedisRateLimiter(settings, client=fake)

    limiter.check("team-a")
    assert list(fake.expirations.values()) == [60]

    fake.expirations.clear()
    allowed, _ = limiter.check("team-a")
    assert allowed
    assert list(fake.expirations.values()) == [60]


def test_response_cache_backend_outage_degrades_to_miss():
    # The response cache is an optimization: with Redis down, chat completions must
    # still succeed via the runtime instead of failing with a 500.
    from app.cache import RedisResponseCache

    settings = _tool_settings(response_cache_enabled=True)
    app = create_app(settings)
    fake_runtime = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    app.state.runtime_client = fake_runtime

    class _BrokenCacheRedis:
        def get(self, key):
            raise OSError("redis down")

        def set(self, key, value, ex=None):
            raise OSError("redis down")

    app.state.response_cache = RedisResponseCache(
        _tool_settings(response_cache_backend="redis", response_cache_redis_url="redis://budget-redis:6379/0"),
        client=_BrokenCacheRedis(),
    )
    client = TestClient(app)

    response = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 200
    assert response.headers["X-Cache"] == "MISS"
    assert fake_runtime.calls == 1


def test_cache_hit_does_not_recount_token_usage_metrics():
    # A cache hit consumes no runtime tokens; re-counting the cached usage would
    # make Prometheus cost/token series disagree with /v1/usage (FinOps drift).
    from prometheus_client import REGISTRY

    app = create_app(_tool_settings(response_cache_enabled=True))
    app.state.runtime_client = FakeRuntimeClient(
        response={
            "id": "x",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }
    )
    client = TestClient(app)
    body = {"messages": [{"role": "user", "content": "cache metric probe"}]}

    assert client.post("/v1/chat/completions", json=body).headers["X-Cache"] == "MISS"
    after_miss = REGISTRY.get_sample_value(
        "inference_gateway_tokens_total", {"backend": "vllm", "token_type": "total_tokens"}
    )
    assert client.post("/v1/chat/completions", json=body).headers["X-Cache"] == "HIT"
    after_hit = REGISTRY.get_sample_value(
        "inference_gateway_tokens_total", {"backend": "vllm", "token_type": "total_tokens"}
    )

    assert after_hit == after_miss


def test_moderations_rejects_oversized_input():
    # Moderations shares the admission ceiling; without it this endpoint would run
    # every classifier regex over an arbitrarily large body.
    app = create_app(_tool_settings(max_prompt_chars=64))
    client = TestClient(app)

    response = client.post("/v1/moderations", json={"input": "x" * 100})

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "input_too_large"
