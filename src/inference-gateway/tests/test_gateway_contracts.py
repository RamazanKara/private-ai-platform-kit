import asyncio
import hashlib
import json
import logging

import httpx
from app.main import create_app
from app.runtime_client import RuntimeClient
from app.settings import Settings
from fastapi.testclient import TestClient

from tests.gateway_support import (
    FakeRuntimeClient,
    _budget_header_settings,
    _retry_settings,
    _tool_settings,
)


def test_admission_error_uses_openai_envelope_and_preserves_detail():
    # An admission 400 carries the OpenAI-style error object AND the legacy detail
    # (back-comfort) so callers can migrate off detail.reason gradually.
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
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["code"] == "too_many_tools"
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    # Legacy shape preserved this release.
    assert body["detail"]["reason"] == "too_many_tools"


def test_budget_429_envelope_is_rate_limit_error_and_keeps_retry_after():
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
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"X-Sandbox-ID": "budget-lab"},
        json={"messages": [{"role": "user", "content": "hello"}], "max_tokens": 4},
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "86400"
    body = response.json()
    assert body["error"]["type"] == "rate_limit_error"
    assert body["error"]["code"] == "sandbox_token_budget_exceeded"
    assert body["detail"]["reason"] == "sandbox_token_budget_exceeded"


def test_auth_401_envelope_is_authentication_error():
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
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    body = response.json()
    assert body["error"]["type"] == "authentication_error"
    assert body["error"]["code"] == "invalid_or_missing_api_key"
    assert body["detail"]["reason"] == "invalid_or_missing_api_key"


def test_unknown_route_404_uses_openai_envelope():
    # FastAPI's own 404 is reshaped by the StarletteHTTPException handler too.
    app = create_app(_tool_settings())
    client = TestClient(app)

    response = client.get("/v1/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["type"] == "not_found_error"


def test_pydantic_422_left_in_default_shape():
    # Validation errors go through FastAPI's RequestValidationError handler, which we
    # intentionally do not reshape; the body stays in its default {"detail": [...]} form.
    app = create_app(_tool_settings())
    client = TestClient(app)

    response = client.post("/v1/chat/completions", json={"messages": "not-a-list"})

    assert response.status_code == 422
    assert "error" not in response.json()


# --- /v1/batch-inference synchronous fan-out (task 3.2) ---


def test_batch_inference_works():
    app = create_app(_tool_settings())
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)

    response = client.post(
        "/v1/batch-inference",
        json={
            "requests": [
                {"messages": [{"role": "user", "content": "one"}]},
                {"messages": [{"role": "user", "content": "two"}]},
            ]
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["object"] == "batch"
    assert result["count"] == 2
    assert "Deprecation" not in response.headers


# --- /v1/moderations taxonomy marker (task 3.3) ---


def test_moderations_response_carries_governance_taxonomy_marker():
    app = create_app(_tool_settings())
    client = TestClient(app)

    response = client.post("/v1/moderations", json={"input": "text to classify"})

    assert response.status_code == 200
    assert response.json()["taxonomy"] == "governance"


# --- governed /v1/completions (task 3.4) ---


def test_completions_admitted_budgeted_audited_and_forwarded(caplog):
    caplog.set_level(logging.INFO, logger="ai_platform_ops_lab.audit")
    app = create_app(
        _tool_settings(
            allowed_models=("default-model",),
            sandbox_budget_enabled=True,
            sandbox_request_budget=5,
            sandbox_estimated_token_budget=1000,
            budget_estimated_chars_per_token=4,
        )
    )
    fake = FakeRuntimeClient(
        response={
            "id": "cmpl-test",
            "object": "text_completion",
            "choices": [{"text": "hello world", "index": 0, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
    )
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/completions",
        headers={"X-Sandbox-ID": "budget-lab"},
        json={"prompt": "hello", "max_tokens": 5},
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["text"] == "hello world"
    # Forwarded to the runtime's /v1/completions with the resolved model.
    assert fake.payload["prompt"] == "hello"
    assert fake.payload["model"] == "default-model"
    # Budgeted (headers present).
    assert response.headers["x-ratelimit-remaining-requests"] == "4"
    # Audited as an inference_request receipt with the prompt fingerprint.
    event = next(
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "ai_platform_ops_lab.audit" and '"inference_request"' in record.getMessage()
    )
    assert event["input_count"] == 1
    assert event["prompt_chars"] == 5


def test_completions_rejects_over_cap_max_tokens():
    app = create_app(_tool_settings(allowed_models=("default-model",), max_completion_tokens=10))
    app.state.runtime_client = FakeRuntimeClient(response={"object": "text_completion", "choices": []})
    client = TestClient(app)

    response = client.post("/v1/completions", json={"prompt": "hi", "max_tokens": 50})

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "max_tokens_too_large"
    assert body["detail"]["reason"] == "max_tokens_too_large"


def test_completions_rejects_prompt_secret_in_block_mode():
    app = create_app(_tool_settings(allowed_models=("default-model",)))
    app.state.runtime_client = FakeRuntimeClient(response={"object": "text_completion", "choices": []})
    client = TestClient(app)

    response = client.post(
        "/v1/completions",
        json={"prompt": "token ghp_0123456789abcdefghijABCDEFGHIJ012345"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "prompt_secret_detected"


def test_completions_redacts_prompt_secret_in_redact_mode():
    app = create_app(_tool_settings(allowed_models=("default-model",), prompt_secret_mode="redact"))
    fake = FakeRuntimeClient(response={"object": "text_completion", "choices": [{"text": "ok"}]})
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/completions",
        json={"prompt": "token ghp_0123456789abcdefghijABCDEFGHIJ012345"},
    )

    assert response.status_code == 200
    assert response.headers["X-Prompt-Guardrail"] == "redacted"
    # The credential is redacted before the payload reaches the runtime.
    assert "ghp_0123456789abcdefghijABCDEFGHIJ012345" not in fake.payload["prompt"]
    assert "[REDACTED:github_token]" in fake.payload["prompt"]


def test_completions_rejects_streaming_with_clear_error():
    app = create_app(_tool_settings(allowed_models=("default-model",), allow_streaming=True))
    app.state.runtime_client = FakeRuntimeClient(response={"object": "text_completion", "choices": []})
    client = TestClient(app)

    response = client.post("/v1/completions", json={"prompt": "hi", "stream": True})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "streaming_not_supported"


def test_completions_rejects_unapproved_model():
    app = create_app(_tool_settings(allowed_models=("default-model",)))
    app.state.runtime_client = FakeRuntimeClient(response={"object": "text_completion", "choices": []})
    client = TestClient(app)

    response = client.post("/v1/completions", json={"model": "rogue-model", "prompt": "hi"})

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "model_not_allowed"


def test_completion_output_guardrail_redacts_leaked_secret_in_text():
    app = create_app(
        _tool_settings(
            allowed_models=("default-model",),
            output_guardrail_enabled=True,
            output_guardrail_mode="redact",
        )
    )
    fake = FakeRuntimeClient(
        response={
            "object": "text_completion",
            "choices": [{"text": "leaked ghp_0123456789abcdefghijABCDEFGHIJ012345 done"}],
        }
    )
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post("/v1/completions", json={"prompt": "generate a token"})

    assert response.status_code == 200
    assert response.headers["X-Output-Guardrail"] == "redacted"
    assert "[REDACTED:github_token]" in response.json()["choices"][0]["text"]


def test_runtime_client_completions_targets_completions_endpoint(monkeypatch):
    seen = {}

    class RecordingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def post(self, url, json=None, headers=None):
            seen["url"] = url
            request = httpx.Request("POST", url)
            return httpx.Response(200, request=request, json={"object": "text_completion", "choices": [{"text": "ok"}]})

    monkeypatch.setattr(httpx, "AsyncClient", RecordingClient)
    client = RuntimeClient(_retry_settings())

    response = asyncio.run(client.completions({"prompt": "hi"}))

    assert seen["url"] == "http://ollama:11434/v1/completions"
    assert response["choices"][0]["text"] == "ok"


# --- Phase 3 review fixes: /v1/completions budget parity with chat ----------


def test_completions_budget_charges_completion_cap_like_chat():
    settings = _budget_header_settings(max_completion_tokens=100)
    app = create_app(settings)
    fake = FakeRuntimeClient(response={"id": "x", "object": "text_completion", "choices": [{"text": "hi"}]})
    app.state.runtime_client = fake
    client = TestClient(app)

    resp = client.post("/v1/completions", headers={"X-Sandbox-ID": "budget-lab"}, json={"prompt": "hello"})

    assert resp.status_code == 200
    # No max_tokens given, so - exactly like chat - the completion cap (100) is charged:
    # ceil(5 chars / 4) = 2 prompt + 100 cap = 102; remaining 1000 - 102 = 898.
    assert resp.headers["x-ratelimit-remaining-tokens"] == "898"


def test_completions_budget_multiplies_by_n():
    settings = _budget_header_settings(max_completion_tokens=10, max_completions_per_request=5)
    app = create_app(settings)
    fake = FakeRuntimeClient(response={"id": "x", "object": "text_completion", "choices": [{"text": "hi"}]})
    app.state.runtime_client = fake
    client = TestClient(app)

    resp = client.post(
        "/v1/completions",
        headers={"X-Sandbox-ID": "budget-lab"},
        json={"prompt": "hello", "max_tokens": 10, "n": 3},
    )

    assert resp.status_code == 200
    # ceil(5/4)=2 prompt + 10 completion * n=3 = 32; remaining 1000 - 32 = 968.
    assert resp.headers["x-ratelimit-remaining-tokens"] == "968"


def test_completions_caps_n_like_chat():
    app = create_app(_tool_settings(max_completions_per_request=1))
    fake = FakeRuntimeClient(response={"id": "x", "object": "text_completion", "choices": [{"text": "hi"}]})
    app.state.runtime_client = fake
    client = TestClient(app)

    resp = client.post("/v1/completions", json={"prompt": "hi", "n": 5})

    assert resp.status_code == 400
    # Also exercises the OpenAI-shaped error envelope: reason surfaces as error.code.
    assert resp.json()["error"]["code"] == "too_many_completions"
    assert resp.json()["detail"]["reason"] == "too_many_completions"
    assert fake.calls == 0
