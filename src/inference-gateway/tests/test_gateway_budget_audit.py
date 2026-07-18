import json
import logging

import pytest
from app.budget import RedisSandboxBudgetTracker
from app.main import create_app
from app.settings import AdmissionPolicyError, Settings
from fastapi.testclient import TestClient

from tests.gateway_support import (
    FakeRedisBudgetStore,
    FakeRuntimeClient,
    _budget_header_settings,
    _tool_settings,
)


def test_sandbox_budget_rejects_estimated_token_overage_before_runtime():
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="approved-model",
        request_timeout_seconds=5,
        allowed_models=("approved-model",),
        max_completion_tokens=10,
        sandbox_budget_enabled=True,
        sandbox_estimated_token_budget=5,
        budget_estimated_chars_per_token=4,
    )
    app = create_app(settings)
    fake = FakeRuntimeClient(
        response={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        }
    )
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"X-Sandbox-ID": "budget-lab"},
        json={"messages": [{"role": "user", "content": "hello"}], "max_tokens": 4},
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "86400"
    assert response.json()["detail"]["reason"] == "sandbox_token_budget_exceeded"
    assert fake.calls == 0


def test_chat_completion_reports_ratelimit_budget_headers():
    app = create_app(_budget_header_settings())
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"X-Sandbox-ID": "budget-lab"},
        json={"messages": [{"role": "user", "content": "hello"}], "max_tokens": 5},
    )

    assert response.status_code == 200
    # One request reserved; estimated tokens = ceil(5 chars / 4) + max_tokens 5 = 7.
    assert response.headers["x-ratelimit-limit-requests"] == "5"
    assert response.headers["x-ratelimit-remaining-requests"] == "4"
    assert response.headers["x-ratelimit-limit-tokens"] == "1000"
    assert response.headers["x-ratelimit-remaining-tokens"] == "993"


def test_embeddings_report_ratelimit_budget_headers():
    app = create_app(_budget_header_settings(allowed_models=("default-model",)))
    app.state.runtime_client = FakeRuntimeClient(response={"object": "list", "data": [{"embedding": [0.1]}]})
    client = TestClient(app)

    response = client.post("/v1/embeddings", headers={"X-Sandbox-ID": "budget-lab"}, json={"input": "embed this"})

    assert response.status_code == 200
    # Embedding inputs are budgeted like prompts with max_tokens 0: ceil(10 chars / 4) = 3.
    assert response.headers["x-ratelimit-limit-requests"] == "5"
    assert response.headers["x-ratelimit-remaining-requests"] == "4"
    assert response.headers["x-ratelimit-limit-tokens"] == "1000"
    assert response.headers["x-ratelimit-remaining-tokens"] == "997"


def test_cache_hit_omits_ratelimit_budget_headers():
    app = create_app(_budget_header_settings(response_cache_enabled=True))
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)
    body = {"messages": [{"role": "user", "content": "same question"}], "max_tokens": 5}

    first = client.post("/v1/chat/completions", json=body)
    second = client.post("/v1/chat/completions", json=body)

    assert first.headers["X-Cache"] == "MISS"
    assert first.headers["x-ratelimit-remaining-requests"] == "4"
    # A cache hit consumes no budget, so it reports no budget headers.
    assert second.headers["X-Cache"] == "HIT"
    assert "x-ratelimit-remaining-requests" not in second.headers
    assert "x-ratelimit-limit-tokens" not in second.headers


def test_streaming_response_carries_ratelimit_budget_headers():
    app = create_app(_budget_header_settings(allow_streaming=True))
    app.state.runtime_client = FakeRuntimeClient()
    client = TestClient(app)

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={"stream": True, "messages": [{"role": "user", "content": "hello"}], "max_tokens": 5},
    ) as response:
        response.read()

    assert response.status_code == 200
    assert response.headers["x-ratelimit-limit-requests"] == "5"
    assert response.headers["x-ratelimit-remaining-requests"] == "4"
    assert response.headers["x-ratelimit-remaining-tokens"] == "993"


def test_ratelimit_budget_headers_omitted_when_budget_disabled_or_unlimited():
    runtime_response = {"id": "x", "object": "chat.completion", "choices": []}
    disabled = create_app(_tool_settings())
    disabled.state.runtime_client = FakeRuntimeClient(response=runtime_response)
    unlimited = create_app(_budget_header_settings(sandbox_request_budget=0, sandbox_estimated_token_budget=0))
    unlimited.state.runtime_client = FakeRuntimeClient(response=runtime_response)
    body = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}

    for app in (disabled, unlimited):
        response = TestClient(app).post("/v1/chat/completions", json=body)
        assert response.status_code == 200
        assert not [name for name in response.headers if name.lower().startswith("x-ratelimit-")]


def test_redis_budget_tracker_shares_usage_across_tracker_instances():
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="approved-model",
        request_timeout_seconds=5,
        max_completion_tokens=10,
        sandbox_budget_enabled=True,
        sandbox_budget_backend="redis",
        sandbox_request_budget=1,
        sandbox_prompt_char_budget=1000,
        sandbox_estimated_token_budget=1000,
    )
    store = FakeRedisBudgetStore()
    first_pod = RedisSandboxBudgetTracker(settings, client=store)
    second_pod = RedisSandboxBudgetTracker(settings, client=store)
    payload = {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 5}

    first = first_pod.reserve("shared-lab", payload)
    with pytest.raises(AdmissionPolicyError) as exc:
        second_pod.reserve("shared-lab", payload)
    snapshot = second_pod.snapshot("shared-lab")

    assert first is not None
    assert first.backend == "redis"
    assert first.usage.requests == 1
    assert exc.value.reason == "sandbox_request_budget_exceeded"
    assert snapshot["backend"] == "redis"
    assert snapshot["usage"]["requests"] == 1


def test_redis_budget_uses_fixed_window_without_refreshing_positive_ttl():
    from app.budget import REDIS_RESERVE_SCRIPT

    assert "local existing_ttl = redis.call('TTL', key)" in REDIS_RESERVE_SCRIPT
    assert "if ttl > 0 and existing_ttl < 0 then" in REDIS_RESERVE_SCRIPT


def test_budget_counts_tool_call_arguments_as_prompt_context():
    from app.budget import budget_delta

    settings = _tool_settings(budget_estimated_chars_per_token=4)
    payload = {
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call", "type": "function", "function": {"name": "f", "arguments": "x" * 40}}],
            }
        ],
        "max_tokens": 0,
    }

    delta = budget_delta(settings, payload)

    assert delta.prompt_chars >= 40
    assert delta.estimated_tokens >= 10


def test_audit_log_redacts_prompt_content(caplog):
    caplog.set_level(logging.INFO, logger="ai_platform_ops_lab.audit")
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
    )
    app = create_app(settings)
    app.state.runtime_client = FakeRuntimeClient(
        response={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        }
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"X-Request-ID": "audit-1"},
        json={"messages": [{"role": "user", "content": "secret customer prompt"}]},
    )

    assert response.status_code == 200
    assert "audit-1" in caplog.text
    assert "prompt_sha256" in caplog.text
    assert "secret customer prompt" not in caplog.text


def test_audit_events_carry_agent_action_receipts(caplog):
    caplog.set_level(logging.INFO, logger="ai_platform_ops_lab.audit")
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
        max_prompt_chars=64,
    )
    app = create_app(settings)
    app.state.runtime_client = FakeRuntimeClient(
        response={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        }
    )
    client = TestClient(app)

    allowed = client.post(
        "/v1/chat/completions",
        headers={"X-Request-ID": "receipt-allowed", "X-Sandbox-ID": "agent-lab"},
        json={"messages": [{"role": "user", "content": "short prompt"}]},
    )
    denied = client.post(
        "/v1/chat/completions",
        headers={"X-Request-ID": "receipt-denied", "X-Sandbox-ID": "agent-lab"},
        json={"messages": [{"role": "user", "content": "x" * 200}]},
    )

    assert allowed.status_code == 200
    assert denied.status_code == 400
    events = [
        json.loads(record.getMessage()) for record in caplog.records if record.name == "ai_platform_ops_lab.audit"
    ]
    receipts = {event["request_id"]: event for event in events if event.get("event") == "inference_request"}
    assert receipts["receipt-allowed"]["action_type"] == "model_call"
    assert receipts["receipt-allowed"]["decision"] == "allowed"
    assert receipts["receipt-allowed"]["sandbox_id"] == "agent-lab"
    assert receipts["receipt-denied"]["action_type"] == "model_call"
    assert receipts["receipt-denied"]["decision"] == "denied"
    assert receipts["receipt-denied"]["error"]
    # Receipts stay chained: both records must carry chain hashes.
    assert receipts["receipt-allowed"]["record_hash"]
    assert receipts["receipt-denied"]["prev_hash"]
