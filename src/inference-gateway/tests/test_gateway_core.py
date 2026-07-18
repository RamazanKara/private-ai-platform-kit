import hashlib
import json
import logging
import time

import httpx
from app.main import create_app
from app.policy import ModelRoute, ModelRoutingPolicy
from app.settings import Settings
from fastapi.testclient import TestClient

from tests.gateway_support import (
    FakeRuntimeClient,
    _tool_settings,
)


def test_healthz_reports_backend_and_model():
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="tiny-model",
        request_timeout_seconds=5,
    )
    client = TestClient(create_app(settings))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "backend": "ollama",
        "model": "tiny-model",
    }


def test_v1_models_lists_allowed_models():
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
        allowed_models=("default-model", "coder-model"),
    )
    client = TestClient(create_app(settings))

    response = client.get("/v1/models")

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert [item["id"] for item in body["data"]] == ["default-model", "coder-model"]


def test_readyz_reports_runtime_health_without_backend_urls():
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama.internal:11434",
        vllm_base_url="http://vllm.internal:8000",
        model_id="default-model",
        request_timeout_seconds=5,
        allowed_models=("default-model",),
    )
    app = create_app(settings)
    fake = FakeRuntimeClient()
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["models"] == ["default-model"]
    assert body["runtimes"]["ollama"]["status"] == "ok"
    assert "internal" not in response.text
    assert fake.health_backends == ["ollama"]


def test_readyz_returns_503_when_runtime_is_unavailable():
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama.internal:11434",
        vllm_base_url="http://vllm.internal:8000",
        model_id="default-model",
        request_timeout_seconds=5,
        allowed_models=("default-model",),
    )
    app = create_app(settings)
    app.state.runtime_client = FakeRuntimeClient(error=httpx.ConnectError("no route"))
    client = TestClient(app)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert "no route" not in response.text


def test_readyz_stays_ready_when_primary_runtime_has_healthy_fallback():
    settings = _tool_settings(runtime_backend="vllm", allowed_models=("primary", "fallback"), model_id="primary")
    app = create_app(settings)
    app.state.model_routing_policy = ModelRoutingPolicy(
        routes=(
            ModelRoute("primary", "vllm", fallbacks=("fallback",)),
            ModelRoute("fallback", "ollama"),
        )
    )

    class BackendHealth:
        async def health(self, backend=None):
            if backend == "vllm":
                raise httpx.ConnectError("primary unavailable")
            return {"status": "ok"}

    app.state.runtime_client = BackendHealth()
    response = TestClient(app).get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["runtimes"]["vllm"]["status"] == "unavailable"
    assert response.json()["model_status"]["primary"] == {"status": "ok", "ready_via": "ollama"}


def test_readyz_returns_503_when_required_redis_dependency_is_unavailable():
    settings = _tool_settings(
        sandbox_budget_enabled=True,
        sandbox_budget_backend="redis",
        sandbox_budget_redis_url="redis://redis.internal:6379/0",
    )
    app = create_app(settings)
    app.state.runtime_client = FakeRuntimeClient()

    class UnavailableRedis:
        def ping(self):
            raise OSError("redis is down")

    app.state.budget_tracker.client = UnavailableRedis()
    response = TestClient(app).get("/readyz")

    assert response.status_code == 503
    assert response.json()["dependencies"]["budget_store"] == {
        "status": "unavailable",
        "backend": "redis",
    }
    assert "redis is down" not in response.text


def test_chat_completion_forwards_openai_payload():
    settings = Settings(
        runtime_backend="vllm",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
    )
    app = create_app(settings)
    fake = FakeRuntimeClient(
        response={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "hello from vLLM"}}],
        }
    )
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "custom-model",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "hello from vLLM"
    assert fake.payload["model"] == "custom-model"
    assert fake.payload["messages"][0]["content"] == "hello"


def test_chat_completion_forwards_tool_calling_fields():
    # Regression: ChatCompletionRequest dropped tools/tool_choice (extra="ignore"),
    # so coding agents had their function calls silently swallowed before the runtime.
    app = create_app(_tool_settings())
    fake = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    app.state.runtime_client = fake
    client = TestClient(app)

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }
    ]
    response = client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "weather in berlin?"}],
            "tools": tools,
            "tool_choice": "auto",
            "response_format": {"type": "json_object"},
        },
    )

    assert response.status_code == 200
    assert fake.payload["tools"] == tools
    assert fake.payload["tool_choice"] == "auto"
    assert fake.payload["response_format"] == {"type": "json_object"}


def test_chat_completion_forwards_assistant_tool_calls_and_tool_results():
    app = create_app(_tool_settings())
    fake = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {"role": "user", "content": "weather in berlin?"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": '{"city": "berlin"}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "18C and sunny"},
            ],
        },
    )

    assert response.status_code == 200
    forwarded = fake.payload["messages"]
    assert forwarded[1]["tool_calls"][0]["id"] == "call_1"
    assert forwarded[2]["tool_call_id"] == "call_1"


def test_chat_completion_forwards_vision_content_parts():
    # Regression: Message.content was a bare str, so an OpenAI content-part array
    # (text + image_url) failed validation before reaching a vision-capable runtime.
    app = create_app(_tool_settings())
    fake = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    app.state.runtime_client = fake
    client = TestClient(app)

    content = [
        {"type": "text", "text": "what is in this image?"},
        {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}},
    ]
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": content}]},
    )

    assert response.status_code == 200
    assert fake.payload["messages"][0]["content"] == content


def test_chat_completion_forwards_unknown_sampling_params():
    # extra="allow" makes the gateway a faithful OpenAI proxy instead of silently
    # dropping any field it does not model explicitly.
    app = create_app(_tool_settings())
    fake = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "hello"}],
            "top_p": 0.9,
            "seed": 42,
            "stop": ["\n\n"],
        },
    )

    assert response.status_code == 200
    assert fake.payload["top_p"] == 0.9
    assert fake.payload["seed"] == 42
    assert fake.payload["stop"] == ["\n\n"]


def test_chat_completion_rejects_too_many_tools():
    app = create_app(_tool_settings(max_tools=1))
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {"type": "function", "function": {"name": "a"}},
                {"type": "function", "function": {"name": "b"}},
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "too_many_tools"


def test_chat_completion_rejects_oversized_tools():
    app = create_app(_tool_settings(max_tool_chars=64))
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "a", "description": "x" * 200}}],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "tools_too_large"


def test_embeddings_forwards_through_gateway_controls():
    # Regression: the gateway exposed no embeddings route, so embeddings bypassed its
    # auth/budget/audit/model controls entirely.
    app = create_app(_tool_settings(allowed_models=("default-model",)))
    fake = FakeRuntimeClient(
        response={"object": "list", "data": [{"embedding": [0.1, 0.2]}], "usage": {"prompt_tokens": 3}}
    )
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post("/v1/embeddings", json={"input": "embed this text"})

    assert response.status_code == 200
    assert response.json()["data"][0]["embedding"] == [0.1, 0.2]
    assert fake.payload["input"] == "embed this text"
    assert fake.payload["model"] == "default-model"


def test_embeddings_rejects_unapproved_model():
    app = create_app(_tool_settings(allowed_models=("default-model",)))
    app.state.runtime_client = FakeRuntimeClient(response={"object": "list", "data": []})
    client = TestClient(app)

    response = client.post("/v1/embeddings", json={"model": "rogue-model", "input": "hi"})

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "model_not_allowed"


def test_embeddings_rejects_secret_input():
    app = create_app(_tool_settings(allowed_models=("default-model",)))
    app.state.runtime_client = FakeRuntimeClient(response={"object": "list", "data": []})
    client = TestClient(app)

    response = client.post(
        "/v1/embeddings",
        json={"input": "ghp_0123456789abcdefghijABCDEFGHIJ012345"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "prompt_secret_detected"


def test_embeddings_records_input_audit_fingerprint(caplog):
    caplog.set_level(logging.INFO, logger="ai_platform_ops_lab.audit")
    app = create_app(_tool_settings(allowed_models=("default-model",)))
    app.state.runtime_client = FakeRuntimeClient(
        response={"object": "list", "data": [{"embedding": [0.0]}], "usage": {"prompt_tokens": 2}}
    )
    client = TestClient(app)

    response = client.post("/v1/embeddings", json={"input": ["alpha", "beta"]})

    assert response.status_code == 200
    audit = [r.getMessage() for r in caplog.records if '"event": "inference_request"' in r.getMessage()][-1]
    event = json.loads(audit)
    assert event["input_count"] == 2
    assert "prompt_sha256" in event
    assert "alpha" not in audit


def test_rate_limit_rejects_burst_per_sandbox():
    app = create_app(
        _tool_settings(
            rate_limit_enabled=True,
            rate_limit_requests_per_window=1,
            rate_limit_window_seconds=60,
        )
    )
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)
    body = {"messages": [{"role": "user", "content": "hi"}]}

    first = client.post("/v1/chat/completions", headers={"X-Sandbox-ID": "team-a"}, json=body)
    second = client.post("/v1/chat/completions", headers={"X-Sandbox-ID": "team-a"}, json=body)
    other_sandbox = client.post("/v1/chat/completions", headers={"X-Sandbox-ID": "team-b"}, json=body)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"]["reason"] == "rate_limited"
    assert int(second.headers["Retry-After"]) >= 1
    # The limit is per sandbox, so a different sandbox is not throttled.
    assert other_sandbox.status_code == 200


def test_concurrency_limit_sheds_load():
    app = create_app(_tool_settings(max_concurrent_requests=1))
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    # Simulate one request already in flight so the next is shed.
    app.state.inflight = 1
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "concurrency_limit"
    assert int(response.headers["Retry-After"]) >= 1


def test_response_cache_returns_cached_without_second_runtime_call():
    app = create_app(_tool_settings(response_cache_enabled=True))
    fake = FakeRuntimeClient(
        response={"id": "x", "object": "chat.completion", "choices": [{"message": {"content": "cached"}}]}
    )
    app.state.runtime_client = fake
    client = TestClient(app)
    body = {"messages": [{"role": "user", "content": "same question"}]}

    first = client.post("/v1/chat/completions", json=body)
    second = client.post("/v1/chat/completions", json=body)

    assert first.status_code == 200
    assert first.headers["X-Cache"] == "MISS"
    assert second.status_code == 200
    assert second.headers["X-Cache"] == "HIT"
    assert second.json()["choices"][0]["message"]["content"] == "cached"
    assert fake.calls == 1


def test_response_cache_is_per_sandbox():
    app = create_app(_tool_settings(response_cache_enabled=True))
    fake = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    app.state.runtime_client = fake
    client = TestClient(app)
    body = {"messages": [{"role": "user", "content": "q"}]}

    client.post("/v1/chat/completions", headers={"X-Sandbox-ID": "team-a"}, json=body)
    other = client.post("/v1/chat/completions", headers={"X-Sandbox-ID": "team-b"}, json=body)

    assert other.headers["X-Cache"] == "MISS"
    assert fake.calls == 2


def test_response_cache_disabled_by_default():
    app = create_app(_tool_settings())
    fake = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    app.state.runtime_client = fake
    client = TestClient(app)
    body = {"messages": [{"role": "user", "content": "q"}]}

    first = client.post("/v1/chat/completions", json=body)
    client.post("/v1/chat/completions", json=body)

    assert "X-Cache" not in first.headers
    assert fake.calls == 2


def test_audit_events_form_tamper_evident_chain(caplog):
    caplog.set_level(logging.INFO, logger="ai_platform_ops_lab.audit")
    app = create_app(_tool_settings())
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)
    body = {"messages": [{"role": "user", "content": "hi"}]}

    started_at = time.time()
    client.post("/v1/chat/completions", json=body)
    client.post("/v1/chat/completions", json=body)
    finished_at = time.time()

    events = [json.loads(r.getMessage()) for r in caplog.records if '"event": "inference_request"' in r.getMessage()]
    assert len(events) >= 2
    genesis = hashlib.sha256(b"genesis").hexdigest()
    prev = genesis
    for event in events:
        # WHEN is chain-covered, not just WHAT: every event carries a wall-clock ts
        # inside the hashed record (range-checked only; time.time() is not monotonic).
        assert isinstance(event["ts"], float)
        assert started_at <= event["ts"] <= finished_at
        # The per-process chain_id is present and hash-covered (part of the record the
        # record_hash is computed over), so the verifier can group per-replica chains.
        assert event["chain_id"] == app.state.audit_chain_id
        assert event["prev_hash"] == prev
        record = {k: v for k, v in event.items() if k not in ("prev_hash", "record_hash")}
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        expected = hashlib.sha256(prev.encode("ascii") + canonical).hexdigest()
        assert event["record_hash"] == expected
        prev = event["record_hash"]


def test_rate_limit_disabled_by_default_allows_burst():
    app = create_app(_tool_settings())
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)
    body = {"messages": [{"role": "user", "content": "hi"}]}

    for _ in range(5):
        assert client.post("/v1/chat/completions", json=body).status_code == 200


def test_moderations_flags_credentials_and_pii():
    app = create_app(_tool_settings())
    client = TestClient(app)

    response = client.post(
        "/v1/moderations",
        json={"input": "key ghp_0123456789abcdefghijABCDEFGHIJ012345 ssn 123-45-6789"},
    )

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["flagged"] is True
    assert result["categories"]["credential"] is True
    assert result["categories"]["pii"] is True


def test_moderations_clean_input_not_flagged():
    app = create_app(_tool_settings())
    client = TestClient(app)

    response = client.post("/v1/moderations", json={"input": "what is the capital of France?"})

    assert response.status_code == 200
    assert response.json()["results"][0]["flagged"] is False


def test_chat_rejects_blocked_content_term():
    app = create_app(_tool_settings(blocked_content_terms=("projectx",)))
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "tell me about ProjectX roadmap"}]},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "content_blocked"


def test_email_not_blocked_by_default():
    app = create_app(_tool_settings())
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "reach me at user@example.com"}]},
    )

    # PII detectors are opt-in: an email must not be rejected with the default config.
    assert response.status_code == 200


def test_pii_email_rejected_when_enabled():
    app = create_app(_tool_settings(prompt_secret_patterns=("email",)))
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "reach me at user@example.com"}]},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "prompt_secret_detected"
