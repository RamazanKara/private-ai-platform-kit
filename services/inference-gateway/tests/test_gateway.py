import hashlib
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app.budget import RedisSandboxBudgetTracker
from app.main import create_app
from app.settings import AdmissionPolicyError, Settings


class FakeRuntimeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.payload = None
        self.headers = None
        self.calls = 0

    async def chat_completions(self, payload, headers=None):
        self.calls += 1
        self.payload = payload
        self.headers = headers or {}
        if self.error:
            raise self.error
        return self.response


class FakeRedisBudgetStore:
    def __init__(self):
        self.data = {}

    def hgetall(self, key):
        return dict(self.data.get(key, {}))

    def ttl(self, key):
        return 86400 if key in self.data else -2

    def eval(self, script, numkeys, key, ttl, add_requests, add_prompt_chars, add_estimated_tokens, limit_requests, limit_prompt_chars, limit_estimated_tokens):
        current = self.data.get(
            key,
            {"requests": 0, "prompt_chars": 0, "estimated_tokens": 0},
        )
        proposed = {
            "requests": current["requests"] + int(add_requests),
            "prompt_chars": current["prompt_chars"] + int(add_prompt_chars),
            "estimated_tokens": current["estimated_tokens"] + int(add_estimated_tokens),
        }
        checks = (
            ("requests", int(limit_requests), "sandbox_request_budget_exceeded", "request"),
            ("prompt_chars", int(limit_prompt_chars), "sandbox_prompt_budget_exceeded", "prompt character"),
            ("estimated_tokens", int(limit_estimated_tokens), "sandbox_token_budget_exceeded", "estimated token"),
        )
        for field, limit, reason, label in checks:
            if limit > 0 and proposed[field] > limit:
                return [0, reason, label, proposed[field], limit]
        self.data[key] = proposed
        return [
            1,
            proposed["requests"],
            proposed["prompt_chars"],
            proposed["estimated_tokens"],
        ]


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
            "choices": [
                {"message": {"role": "assistant", "content": "hello from vLLM"}}
            ],
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


def test_chat_completion_uses_default_model_when_model_is_omitted():
    settings = Settings(
        runtime_backend="ollama",
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
            "choices": [
                {"message": {"role": "assistant", "content": "hello from Ollama"}}
            ],
        }
    )
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    assert fake.payload["messages"][0]["role"] == "user"
    assert "model" not in fake.payload


def test_chat_completion_propagates_trace_headers_and_returns_request_context():
    settings = Settings(
        runtime_backend="ollama",
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
            "choices": [
                {"message": {"role": "assistant", "content": "traceable response"}}
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
    )
    app.state.runtime_client = fake
    client = TestClient(app)
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    response = client.post(
        "/v1/chat/completions",
        headers={
            "X-Request-ID": "req-123",
            "X-Sandbox-ID": "team-a-lab",
            "traceparent": traceparent,
        },
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-123"
    assert response.headers["X-Sandbox-ID"] == "team-a-lab"
    assert response.headers["traceparent"] == traceparent
    assert fake.headers["X-Request-ID"] == "req-123"
    assert fake.headers["X-Sandbox-ID"] == "team-a-lab"
    assert fake.headers["traceparent"] == traceparent


def test_chat_completion_rejects_invalid_sandbox_id():
    client = TestClient(
        create_app(
            Settings(
                runtime_backend="ollama",
                ollama_base_url="http://ollama:11434",
                vllm_base_url="http://vllm:8000",
                model_id="default-model",
                request_timeout_seconds=5,
            )
        )
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"X-Sandbox-ID": "bad sandbox"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 400
    assert "sandbox id" in response.json()["detail"]


def test_chat_completion_requires_api_key_when_auth_is_enabled():
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
        api_key_auth_enabled=True,
        api_key_sha256s=(hashlib.sha256(b"secret-key").hexdigest(),),
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

    missing = client.post(
        "/v1/chat/completions",
        headers={"X-Request-ID": "auth-missing", "X-Sandbox-ID": "auth-lab"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    wrong = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": "wrong"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    valid = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": "secret-key"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert missing.status_code == 401
    assert missing.headers["X-Request-ID"] == "auth-missing"
    assert missing.headers["X-Sandbox-ID"] == "auth-lab"
    assert missing.json()["detail"]["reason"] == "invalid_or_missing_api_key"
    assert wrong.status_code == 401
    assert valid.status_code == 200
    assert fake.calls == 1


def test_bearer_api_key_is_accepted_when_auth_is_enabled():
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
        api_key_auth_enabled=True,
        api_key_sha256s=(hashlib.sha256(b"bearer-key").hexdigest(),),
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
        headers={"Authorization": "Bearer bearer-key"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    assert fake.calls == 1


def test_chat_completion_rejects_disallowed_model():
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="approved-model",
        request_timeout_seconds=5,
        allowed_models=("approved-model",),
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
        json={
            "model": "unapproved-model",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 400
    assert "ALLOWED_MODELS" in response.json()["detail"]["message"]
    assert fake.payload is None


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (
            {"messages": []},
            "missing_messages",
        ),
        (
            {
                "messages": [
                    {"role": "user", "content": "one"},
                    {"role": "user", "content": "two"},
                ]
            },
            "too_many_messages",
        ),
        (
            {"messages": [{"role": "user", "content": "01234567890"}]},
            "prompt_too_large",
        ),
        (
            {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 11},
            "max_tokens_too_large",
        ),
        (
            {"messages": [{"role": "user", "content": "hello"}], "temperature": 2.5},
            "invalid_temperature",
        ),
        (
            {"messages": [{"role": "user", "content": "hello"}], "stream": True},
            "streaming_disabled",
        ),
    ],
)
def test_admission_policy_rejects_unsafe_or_expensive_requests(payload, reason):
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="approved-model",
        request_timeout_seconds=5,
        allowed_models=("approved-model",),
        max_messages=1,
        max_prompt_chars=10,
        max_completion_tokens=10,
        allow_streaming=False,
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

    response = client.post("/v1/chat/completions", json=payload)

    assert response.status_code == 400
    assert reason in response.text
    assert fake.payload is None


def test_prompt_secret_detection_rejects_credential_material_before_runtime():
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="approved-model",
        request_timeout_seconds=5,
        allowed_models=("approved-model",),
        prompt_secret_detection_enabled=True,
        prompt_secret_patterns=("private_key",),
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
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "Inspect this file:\n-----BEGIN PRIVATE KEY-----\nredacted\n-----END PRIVATE KEY-----",
                }
            ]
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "prompt_secret_detected"
    assert "private_key" in response.json()["detail"]["message"]
    assert "redacted" not in response.text
    assert fake.payload is None


def test_prompt_secret_detection_can_be_disabled_for_controlled_tests():
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="approved-model",
        request_timeout_seconds=5,
        allowed_models=("approved-model",),
        prompt_secret_detection_enabled=False,
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
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "fixture\n-----BEGIN PRIVATE KEY-----\nredacted\n-----END PRIVATE KEY-----",
                }
            ]
        },
    )

    assert response.status_code == 200
    assert fake.calls == 1


def test_sandbox_budget_status_and_request_limit_rejection():
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="approved-model",
        request_timeout_seconds=5,
        allowed_models=("approved-model",),
        sandbox_budget_enabled=True,
        sandbox_request_budget=1,
        sandbox_prompt_char_budget=1000,
        sandbox_estimated_token_budget=1000,
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
    headers = {"X-Sandbox-ID": "budget-lab"}

    first = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"messages": [{"role": "user", "content": "hello"}], "max_tokens": 5},
    )
    budget = client.get("/v1/sandbox/budget", headers=headers)
    second = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"messages": [{"role": "user", "content": "again"}], "max_tokens": 5},
    )

    assert first.status_code == 200
    assert budget.status_code == 200
    assert budget.json()["enabled"] is True
    assert budget.json()["usage"]["requests"] == 1
    assert budget.json()["limits"]["requests"] == 1
    assert second.status_code == 400
    assert second.json()["detail"]["reason"] == "sandbox_request_budget_exceeded"
    assert fake.calls == 1


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

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "sandbox_token_budget_exceeded"
    assert fake.calls == 0


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
