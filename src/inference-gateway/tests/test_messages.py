"""Tests for the native Anthropic Messages API endpoint (POST /v1/messages).

The endpoint translates an Anthropic request to the internal OpenAI chat shape, runs it
through the SAME governance path as /v1/chat/completions, and translates the OpenAI
completion back into an Anthropic Message. These tests assert that path end to end:
admission (max_tokens cap, missing max_tokens), budget, audit, forwarding, the Anthropic
response shape (content blocks / stop_reason / usage), prompt-secret modes, and auth.

The FakeRuntimeClient / _tool_settings / JWT helpers mirror those in test_gateway.py; they
are replicated here so this suite is self-contained (see task 3.5b).
"""

import base64
import hashlib
import hmac
import json
import logging
import time

import httpx
from app.jwt_auth import JwksCache
from app.main import create_app
from app.settings import Settings
from fastapi.testclient import TestClient


class FakeRuntimeClient:
    """Records the payload forwarded to the runtime and returns a canned response."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.payload = None
        self.headers = None
        self.backend = None
        self.calls = 0

    async def chat_completions(self, payload, headers=None, backend=None):
        self.calls += 1
        self.payload = payload
        self.headers = headers or {}
        self.backend = backend
        if self.error:
            raise self.error
        return self.response

    async def health(self, backend=None):
        if self.error:
            raise self.error
        return {"status": "ok", "backend": backend}


def _tool_settings(**overrides):
    base = {
        "runtime_backend": "vllm",
        "ollama_base_url": "http://ollama:11434",
        "vllm_base_url": "http://vllm:8000",
        "model_id": "default-model",
        "request_timeout_seconds": 5,
    }
    base.update(overrides)
    return Settings(**base)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _signed_hs256_jwt(secret: bytes, claims: dict, kid: str = "test-key") -> str:
    header = {"alg": "HS256", "typ": "JWT", "kid": kid}
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_claims = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature = hmac.new(secret, signing_input, "sha256").digest()
    return f"{encoded_header}.{encoded_claims}.{_b64url(signature)}"


def _hs256_jwt_settings(secret, **overrides):
    # The HS256 secret is provided to verification via the JwksCache.keys() mock (the ``k``
    # JWK field), not a settings field - mirroring test_gateway.py's helper. ``secret`` is
    # accepted for symmetry with that helper's signature.
    base = {
        "runtime_backend": "vllm",
        "ollama_base_url": "http://ollama:11434",
        "vllm_base_url": "http://vllm:8000",
        "model_id": "default-model",
        "request_timeout_seconds": 5,
        "jwt_auth_enabled": True,
        "jwt_jwks_url": "https://issuer.example/.well-known/jwks.json",
    }
    base.update(overrides)
    return Settings(**base)


def _chat_response(text="hello from claude", finish_reason="stop", usage=None):
    return {
        "id": "chatcmpl-abc123",
        "object": "chat.completion",
        "model": "default-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage or {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
    }


# --- happy path: admitted, budgeted, audited, forwarded, Anthropic-shaped ---


def test_messages_admitted_budgeted_audited_and_forwarded(caplog):
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
    fake = FakeRuntimeClient(response=_chat_response())
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/messages",
        headers={"X-Sandbox-ID": "budget-lab"},
        json={
            "model": "default-model",
            "system": "You are terse.",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 64,
        },
    )

    assert response.status_code == 200
    body = response.json()
    # Anthropic-shaped response.
    assert body["type"] == "message"
    assert body["role"] == "assistant"
    assert body["model"] == "default-model"
    assert body["content"] == [{"type": "text", "text": "hello from claude"}]
    assert body["stop_reason"] == "end_turn"
    assert body["stop_sequence"] is None
    assert body["usage"] == {"input_tokens": 7, "output_tokens": 5}

    # Forwarded to the runtime as an OpenAI chat payload: system prepended, max_tokens mapped.
    assert fake.payload["model"] == "default-model"
    assert fake.payload["max_tokens"] == 64
    assert fake.payload["messages"][0] == {"role": "system", "content": "You are terse."}
    assert fake.payload["messages"][1] == {"role": "user", "content": "hello"}

    # Budgeted (OpenAI-style budget headers present on the response).
    assert response.headers["x-ratelimit-remaining-requests"] == "4"

    # Audited as an inference_request receipt with the (translated) prompt fingerprint.
    event = next(
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "ai_platform_ops_lab.audit" and '"inference_request"' in record.getMessage()
    )
    assert event["message_count"] == 2
    assert event["message_roles"] == ["system", "user"]


def test_messages_maps_finish_reasons_and_usage_and_tool_calls():
    app = create_app(_tool_settings(allowed_models=("default-model",)))
    fake = FakeRuntimeClient(
        response={
            "id": "chatcmpl-xyz",
            "object": "chat.completion",
            "model": "default-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "get_weather", "arguments": '{"city": "Berlin"}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
        }
    )
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/messages",
        json={
            "messages": [{"role": "user", "content": "weather in Berlin?"}],
            "max_tokens": 128,
            "tools": [
                {
                    "name": "get_weather",
                    "description": "Get the weather",
                    "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    # tool_calls -> tool_use content block; finish_reason tool_calls -> stop_reason tool_use.
    assert body["stop_reason"] == "tool_use"
    assert body["content"] == [{"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"city": "Berlin"}}]
    assert body["usage"] == {"input_tokens": 11, "output_tokens": 3}
    # Anthropic tool definition translated to an OpenAI function tool before forwarding.
    assert fake.payload["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }
    ]


def test_messages_maps_length_finish_reason_to_max_tokens():
    app = create_app(_tool_settings(allowed_models=("default-model",)))
    app.state.runtime_client = FakeRuntimeClient(response=_chat_response(text="truncated", finish_reason="length"))
    client = TestClient(app)

    response = client.post(
        "/v1/messages",
        json={"messages": [{"role": "user", "content": "write a long essay"}], "max_tokens": 16},
    )

    assert response.status_code == 200
    assert response.json()["stop_reason"] == "max_tokens"


# --- Anthropic requires max_tokens; missing it is a 4xx before governance ---


def test_messages_missing_max_tokens_is_rejected():
    app = create_app(_tool_settings(allowed_models=("default-model",)))
    fake = FakeRuntimeClient(response=_chat_response())
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/messages",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    # Pydantic rejects the missing required field before the runtime is ever called.
    assert response.status_code == 422
    assert fake.calls == 0


# --- over-cap max_tokens is rejected by the shared admission cap ---


def test_messages_rejects_over_cap_max_tokens():
    app = create_app(_tool_settings(allowed_models=("default-model",), max_completion_tokens=10))
    fake = FakeRuntimeClient(response=_chat_response())
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/messages",
        json={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 50},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "max_tokens_too_large"
    assert body["detail"]["reason"] == "max_tokens_too_large"
    # The over-cap request never reaches the runtime.
    assert fake.calls == 0


# --- prompt-secret modes are honored on the translated messages ---


def test_messages_rejects_prompt_secret_in_block_mode():
    app = create_app(_tool_settings(allowed_models=("default-model",)))
    fake = FakeRuntimeClient(response=_chat_response())
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/messages",
        json={
            "messages": [{"role": "user", "content": "token ghp_0123456789abcdefghijABCDEFGHIJ012345"}],
            "max_tokens": 64,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "prompt_secret_detected"
    assert fake.calls == 0


def test_messages_redacts_prompt_secret_in_redact_mode():
    app = create_app(_tool_settings(allowed_models=("default-model",), prompt_secret_mode="redact"))
    fake = FakeRuntimeClient(response=_chat_response(text="ok"))
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/messages",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "here is my key ghp_0123456789abcdefghijABCDEFGHIJ012345"}],
                }
            ],
            "max_tokens": 64,
        },
    )

    assert response.status_code == 200
    assert response.headers["X-Prompt-Guardrail"] == "redacted"
    # The credential is redacted in the translated message before it reaches the runtime.
    forwarded = json.dumps(fake.payload["messages"])
    assert "ghp_0123456789abcdefghijABCDEFGHIJ012345" not in forwarded
    assert "[REDACTED:github_token]" in forwarded


# --- streaming is rejected with a clear error (non-streaming-first, like /v1/completions) ---


def test_messages_rejects_streaming_with_clear_error():
    app = create_app(_tool_settings(allowed_models=("default-model",), allow_streaming=True))
    fake = FakeRuntimeClient(response=_chat_response())
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/messages",
        json={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 64, "stream": True},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "streaming_not_supported"
    assert fake.calls == 0


def test_messages_rejects_unapproved_model():
    app = create_app(_tool_settings(allowed_models=("default-model",)))
    app.state.runtime_client = FakeRuntimeClient(response=_chat_response())
    client = TestClient(app)

    response = client.post(
        "/v1/messages",
        json={"model": "rogue-model", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 64},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "model_not_allowed"


# --- auth / tenant binding apply exactly like the other routes ---


def test_messages_requires_api_key_when_enabled():
    settings = _tool_settings(
        allowed_models=("default-model",),
        api_key_auth_enabled=True,
        api_key_sha256s=(hashlib.sha256(b"secret-key").hexdigest(),),
    )
    app = create_app(settings)
    fake = FakeRuntimeClient(response=_chat_response())
    app.state.runtime_client = fake
    client = TestClient(app)

    body = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 64}
    missing = client.post("/v1/messages", json=body)
    wrong = client.post("/v1/messages", headers={"X-API-Key": "wrong"}, json=body)
    valid = client.post("/v1/messages", headers={"X-API-Key": "secret-key"}, json=body)

    assert missing.status_code == 401
    assert missing.json()["detail"]["reason"] == "invalid_or_missing_api_key"
    assert wrong.status_code == 401
    assert valid.status_code == 200
    # Only the authenticated call reaches the runtime.
    assert fake.calls == 1


def test_messages_jwt_tenant_claim_binds_and_mismatch_is_rejected(monkeypatch):
    secret = b"jwt-test-secret"

    async def fake_keys(self):
        return [{"kty": "oct", "kid": "test-key", "k": _b64url(secret)}]

    monkeypatch.setattr(JwksCache, "keys", fake_keys)
    app = create_app(_hs256_jwt_settings(secret, allowed_models=("default-model",), jwt_tenant_claim="sandbox"))
    fake = FakeRuntimeClient(response=_chat_response())
    app.state.runtime_client = fake
    client = TestClient(app)
    token = _signed_hs256_jwt(secret, {"sandbox": "team-a", "exp": int(time.time()) + 300})
    body = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 64}

    # No header: the sandbox is bound from the verified tenant claim.
    bound = client.post("/v1/messages", headers={"Authorization": f"Bearer {token}"}, json=body)
    assert bound.status_code == 200
    assert bound.headers["X-Sandbox-ID"] == "team-a"

    # A contradicting header is rejected with the same reason as the other routes.
    mismatch = client.post(
        "/v1/messages",
        headers={"Authorization": f"Bearer {token}", "X-Sandbox-ID": "team-b"},
        json=body,
    )
    assert mismatch.status_code == 403
    assert mismatch.json()["detail"]["reason"] == "sandbox_identity_mismatch"


def test_messages_runtime_error_maps_to_502():
    app = create_app(_tool_settings(allowed_models=("default-model",)))
    app.state.runtime_client = FakeRuntimeClient(error=httpx.ConnectError("no route"))
    client = TestClient(app)

    response = client.post(
        "/v1/messages",
        json={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 64},
    )

    assert response.status_code == 502
    assert response.json()["error"]["message"] == "runtime request failed"


def test_messages_image_block_is_metered_by_max_image_bytes():
    # An Anthropic image block is translated to the OpenAI image_url shape so the shared
    # max_image_bytes admission cap applies to it (it previously slipped through unmeasured).
    app = create_app(_tool_settings(allowed_models=("default-model",), max_image_bytes=16))
    fake = FakeRuntimeClient(response=_chat_response())
    app.state.runtime_client = fake
    client = TestClient(app)
    oversized = "A" * 400  # ~300 decoded bytes, well over the 16-byte cap

    response = client.post(
        "/v1/messages",
        json={
            "max_tokens": 16,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": oversized}}
                    ],
                }
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "image_too_large"
    assert fake.calls == 0
