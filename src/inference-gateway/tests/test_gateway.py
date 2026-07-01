import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import types

import httpx
import pytest
from app import main as gateway_main
from app.budget import RedisSandboxBudgetTracker
from app.jwt_auth import JwksCache
from app.main import create_app
from app.policy import ModelRoute, ModelRoutingPolicy
from app.runtime_client import RuntimeClient, sanitize_chat_completion
from app.settings import AdmissionPolicyError, Settings
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi.testclient import TestClient


class FakeRuntimeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.stream_chunks = [b'data: {"choices":[]}\n\n']
        self.payload = None
        self.headers = None
        self.backend = None
        self.health_backends = []
        self.calls = 0

    async def chat_completions(self, payload, headers=None, backend=None):
        self.calls += 1
        self.payload = payload
        self.headers = headers or {}
        self.backend = backend
        if self.error:
            raise self.error
        return self.response

    async def stream_chat_completions(self, payload, headers=None, backend=None):
        self.calls += 1
        self.payload = payload
        self.headers = headers or {}
        self.backend = backend
        if self.error:
            raise self.error
        for chunk in self.stream_chunks:
            yield chunk

    async def embeddings(self, payload, headers=None, backend=None):
        self.calls += 1
        self.payload = payload
        self.headers = headers or {}
        self.backend = backend
        if self.error:
            raise self.error
        return self.response

    async def health(self, backend=None):
        self.health_backends.append(backend)
        if self.error:
            raise self.error
        return {"status": "ok", "backend": backend}


class FakeRedisBudgetStore:
    def __init__(self):
        self.data = {}

    def hgetall(self, key):
        return dict(self.data.get(key, {}))

    def ttl(self, key):
        return 86400 if key in self.data else -2

    def eval(
        self,
        script,
        numkeys,
        key,
        ttl,
        add_requests,
        add_prompt_chars,
        add_estimated_tokens,
        limit_requests,
        limit_prompt_chars,
        limit_estimated_tokens,
    ):
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


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _tamper_jwt_signature(token: str) -> str:
    header, claims, signature = token.split(".")
    signature_bytes = bytearray(_b64url_decode(signature))
    signature_bytes[0] ^= 0x01
    return f"{header}.{claims}.{_b64url(bytes(signature_bytes))}"


def _signed_hs256_jwt(secret: bytes, claims: dict, kid: str = "test-key") -> str:
    header = {"alg": "HS256", "typ": "JWT", "kid": kid}
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_claims = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature = hmac.new(secret, signing_input, "sha256").digest()
    return f"{encoded_header}.{encoded_claims}.{_b64url(signature)}"


def _signed_rs256_jwt(private_key, claims: dict, kid: str = "test-rsa") -> str:
    header = {"alg": "RS256", "typ": "JWT", "kid": kid}
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_claims = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{encoded_header}.{encoded_claims}.{_b64url(signature)}"


def _signed_es256_jwt(private_key, claims: dict, kid: str = "test-ec") -> str:
    header = {"alg": "ES256", "typ": "JWT", "kid": kid}
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_claims = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    der_signature = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_signature)
    raw_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"{encoded_header}.{encoded_claims}.{_b64url(raw_signature)}"


def _rsa_jwk(private_key, kid: str = "test-rsa") -> dict:
    public_numbers = private_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64url(public_numbers.n.to_bytes((public_numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64url(public_numbers.e.to_bytes((public_numbers.e.bit_length() + 7) // 8, "big")),
    }


def _ec_jwk(private_key, kid: str = "test-ec") -> dict:
    public_numbers = private_key.public_key().public_numbers()
    return {
        "kty": "EC",
        "kid": kid,
        "use": "sig",
        "alg": "ES256",
        "crv": "P-256",
        "x": _b64url(public_numbers.x.to_bytes(32, "big")),
        "y": _b64url(public_numbers.y.to_bytes(32, "big")),
    }


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

    client.post("/v1/chat/completions", json=body)
    client.post("/v1/chat/completions", json=body)

    events = [json.loads(r.getMessage()) for r in caplog.records if '"event": "inference_request"' in r.getMessage()]
    assert len(events) >= 2
    genesis = hashlib.sha256(b"genesis").hexdigest()
    prev = genesis
    for event in events:
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


class _BackendAwareFake:
    """Fake runtime client that fails on configured backends and records call order."""

    def __init__(self, fail_backends, error):
        self.fail_backends = set(fail_backends)
        self.error = error
        self.backends_called = []

    async def chat_completions(self, payload, headers=None, backend=None):
        self.backends_called.append(backend)
        if backend in self.fail_backends:
            raise self.error
        return {
            "id": "x",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": f"from {backend}"}}],
        }

    async def stream_chat_completions(self, payload, headers=None, backend=None):
        self.backends_called.append(backend)
        if backend in self.fail_backends:
            raise self.error
        # The yield below makes this an async generator; the failing path raises on
        # first iteration before reaching it.
        yield b'data: {"choices":[]}\n\n'

    async def health(self, backend=None):
        return {"status": "ok", "backend": backend}


def _status_error(status_code):
    request = httpx.Request("POST", "http://runtime/v1/chat/completions")
    return httpx.HTTPStatusError("upstream", request=request, response=httpx.Response(status_code, request=request))


def _fallback_app(allow_streaming=False):
    app = create_app(_tool_settings(allow_streaming=allow_streaming))
    app.state.model_routing_policy = ModelRoutingPolicy(
        routes=(
            ModelRoute("primary-model", "vllm", fallbacks=("backup-model",)),
            ModelRoute("backup-model", "ollama"),
        )
    )
    return app


def test_chat_completion_fails_over_to_fallback_runtime():
    app = _fallback_app()
    fake = _BackendAwareFake(fail_backends={"vllm"}, error=_status_error(503))
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"model": "primary-model", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "from ollama"
    assert fake.backends_called == ["vllm", "ollama"]


def test_chat_completion_does_not_fail_over_on_client_error():
    app = _fallback_app()
    fake = _BackendAwareFake(fail_backends={"vllm"}, error=_status_error(400))
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"model": "primary-model", "messages": [{"role": "user", "content": "hi"}]},
    )

    # A 4xx is a client error the fallback runtime would also reject: no failover.
    assert response.status_code == 502
    assert response.json()["detail"]["runtime_status"] == 400
    assert fake.backends_called == ["vllm"]


def test_streaming_fails_over_before_first_byte():
    app = _fallback_app(allow_streaming=True)
    fake = _BackendAwareFake(fail_backends={"vllm"}, error=_status_error(503))
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"stream": True, "model": "primary-model", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    assert fake.backends_called == ["vllm", "ollama"]


def test_usage_endpoint_reports_usage_and_estimated_cost():
    app = create_app(
        _tool_settings(
            sandbox_budget_enabled=True,
            sandbox_estimated_token_budget=1000,
            usd_per_1k_tokens=2.0,
        )
    )
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)

    # Drive some usage, then read it back with the cost estimate.
    client.post(
        "/v1/chat/completions",
        headers={"X-Sandbox-ID": "team-a"},
        json={"messages": [{"role": "user", "content": "hello there"}], "max_tokens": 100},
    )
    usage = client.get("/v1/usage", headers={"X-Sandbox-ID": "team-a"})

    assert usage.status_code == 200
    body = usage.json()
    assert body["sandbox_id"] == "team-a"
    assert body["currency"] == "USD"
    assert body["usd_per_1k_tokens"] == 2.0
    tokens = body["usage"]["estimated_tokens"]
    assert tokens > 0
    assert body["estimated_cost"] == round((tokens / 1000.0) * 2.0, 6)


def test_canary_target_selects_by_roll():
    policy = ModelRoutingPolicy(
        routes=(
            ModelRoute("primary", "vllm", canary_model_id="canary", canary_weight=0.3),
            ModelRoute("canary", "ollama"),
        )
    )
    primary = policy.resolve("primary", "primary")
    assert policy.canary_target(primary, 0.1).model_id == "canary"  # roll < weight
    assert policy.canary_target(primary, 0.5).model_id == "primary"  # roll >= weight


def test_canary_weight_one_routes_all_traffic_to_canary():
    app = create_app(_tool_settings())
    app.state.model_routing_policy = ModelRoutingPolicy(
        routes=(
            ModelRoute("primary-model", "vllm", canary_model_id="canary-model", canary_weight=1.0),
            ModelRoute("canary-model", "ollama"),
        )
    )
    fake = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"model": "primary-model", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    assert fake.payload["model"] == "canary-model"
    assert fake.backend == "ollama"


def test_canary_weight_zero_routes_to_primary():
    app = create_app(_tool_settings())
    app.state.model_routing_policy = ModelRoutingPolicy(
        routes=(
            ModelRoute("primary-model", "vllm", canary_model_id="canary-model", canary_weight=0.0),
            ModelRoute("canary-model", "ollama"),
        )
    )
    fake = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    app.state.runtime_client = fake
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"model": "primary-model", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    assert fake.payload["model"] == "primary-model"


def test_shadow_request_is_scheduled(monkeypatch):
    captured = {}

    def fake_schedule(client, shadow_route, payload, request):
        captured["shadow_model"] = shadow_route.model_id

    monkeypatch.setattr(gateway_main, "_schedule_shadow", fake_schedule)
    app = create_app(_tool_settings())
    app.state.model_routing_policy = ModelRoutingPolicy(
        routes=(
            ModelRoute("primary-model", "vllm", shadow_model_id="shadow-model"),
            ModelRoute("shadow-model", "ollama"),
        )
    )
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"model": "primary-model", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    assert captured["shadow_model"] == "shadow-model"


def test_batch_processes_multiple_requests():
    app = create_app(_tool_settings())
    app.state.runtime_client = FakeRuntimeClient(
        response={"id": "x", "object": "chat.completion", "choices": [{"message": {"content": "ok"}}]}
    )
    client = TestClient(app)

    response = client.post(
        "/v1/batches",
        json={
            "requests": [
                {"messages": [{"role": "user", "content": "one"}]},
                {"messages": [{"role": "user", "content": "two"}]},
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "batch"
    assert body["count"] == 2
    statuses = sorted(item["status_code"] for item in body["results"])
    assert statuses == [200, 200]


def test_batch_rejects_oversized_batch():
    app = create_app(_tool_settings(max_batch_requests=1))
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)

    response = client.post(
        "/v1/batches",
        json={
            "requests": [
                {"messages": [{"role": "user", "content": "one"}]},
                {"messages": [{"role": "user", "content": "two"}]},
            ]
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "batch_too_large"


def test_batch_reports_per_item_errors_without_failing_batch():
    app = create_app(_tool_settings(allowed_models=("approved-model",)))
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)

    response = client.post(
        "/v1/batches",
        json={
            "requests": [
                {"model": "approved-model", "messages": [{"role": "user", "content": "ok"}]},
                {"model": "rogue-model", "messages": [{"role": "user", "content": "bad"}]},
            ]
        },
    )

    assert response.status_code == 200
    results = {item["index"]: item for item in response.json()["results"]}
    assert results[0]["status_code"] == 200
    assert results[1]["status_code"] == 400
    assert results[1]["error"]["reason"] == "model_not_allowed"


def test_chat_completion_metrics_use_endpoint_route_label():
    # Regression: the handler reused the `route` variable for both the Prometheus label
    # ("/v1/chat/completions") and the resolved ModelRoute, so a successful request
    # recorded the ModelRoute repr as the `route` label instead of the request path.
    settings = Settings(
        runtime_backend="vllm",
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
            "choices": [{"message": {"role": "assistant", "content": "hello"}}],
        }
    )
    client = TestClient(app)

    completion = client.post(
        "/v1/chat/completions",
        json={"model": "custom-model", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert completion.status_code == 200

    metrics = client.get("/metrics").text
    assert 'route="/v1/chat/completions"' in metrics
    assert "ModelRoute(" not in metrics


def test_runtime_response_removes_reasoning_metadata():
    payload = {
        "id": "chatcmpl-test",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "hello",
                    "reasoning": "internal reasoning",
                    "reasoning_content": "internal reasoning",
                    "thinking": "internal reasoning",
                }
            }
        ],
    }

    response = sanitize_chat_completion(payload)

    assert response["choices"][0]["message"] == {
        "role": "assistant",
        "content": "hello",
    }
    assert "reasoning" in payload["choices"][0]["message"]


def test_streaming_chat_completion_is_passed_through_when_enabled():
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
        allow_streaming=True,
    )
    app = create_app(settings)
    fake = FakeRuntimeClient()
    fake.stream_chunks = [
        b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    app.state.runtime_client = fake
    client = TestClient(app)

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        },
    ) as response:
        body = response.read()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert b"hel" in body
    assert b"[DONE]" in body
    assert fake.payload["stream"] is True
    assert fake.calls == 1


def test_streaming_chat_completion_records_usage_latency_and_audit(caplog):
    caplog.set_level(logging.INFO, logger="ai_platform_ops_lab.audit")
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
        allow_streaming=True,
    )
    app = create_app(settings)
    fake = FakeRuntimeClient()
    fake.stream_chunks = [
        b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
        # Terminal usage chunk carries the token counts for the streamed request.
        b'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":3,"total_tokens":10}}\n\n',
        b"data: [DONE]\n\n",
    ]
    app.state.runtime_client = fake
    client = TestClient(app)

    def _total_tokens() -> float:
        for line in client.get("/metrics").text.splitlines():
            if line.startswith("inference_gateway_tokens_total") and 'token_type="total_tokens"' in line:
                return float(line.rsplit(" ", 1)[1])
        return 0.0

    before_tokens = _total_tokens()

    with client.stream(
        "POST",
        "/v1/chat/completions",
        headers={"X-Request-ID": "stream-audit-1"},
        json={"stream": True, "messages": [{"role": "user", "content": "hello"}]},
    ) as response:
        body = response.read()

    assert response.status_code == 200
    assert b"[DONE]" in body
    # Audit recording is deferred to true end-of-stream: parse the emitted JSON event.
    audit_event = next(
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "ai_platform_ops_lab.audit" and '"stream-audit-1"' in record.getMessage()
    )
    # The bug recorded status 200, ~0 latency, and no usage before bytes flowed; assert the fix.
    assert audit_event["status_code"] == 200
    assert audit_event["latency_ms"] > 0
    assert audit_event["usage"]["total_tokens"] == 10
    # Token usage is exported to Prometheus from the parsed terminal usage chunk (10 here).
    assert _total_tokens() == before_tokens + 10.0


def test_streaming_mid_stream_upstream_error_emits_terminal_event_and_records_502(caplog):
    caplog.set_level(logging.INFO, logger="ai_platform_ops_lab.audit")

    class MidStreamFailingClient(FakeRuntimeClient):
        async def stream_chat_completions(self, payload, headers=None, backend=None):
            self.calls += 1
            self.payload = payload
            self.headers = headers or {}
            self.backend = backend
            yield b'data: {"choices":[{"delta":{"content":"par"}}]}\n\n'
            raise httpx.ReadError("upstream dropped mid-stream")

    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
        allow_streaming=True,
    )
    app = create_app(settings)
    app.state.runtime_client = MidStreamFailingClient()
    client = TestClient(app)

    def _requests_502() -> float:
        # Sum across backend label-sets: more than one backend can have a 502 series
        # for this route (e.g. once cross-runtime fallback records a fallback backend),
        # so picking the first matching line would be ambiguous.
        total = 0.0
        for line in client.get("/metrics").text.splitlines():
            if (
                line.startswith("inference_gateway_requests_total")
                and 'route="/v1/chat/completions"' in line
                and 'status="502"' in line
            ):
                total += float(line.rsplit(" ", 1)[1])
        return total

    before_502 = _requests_502()

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={"stream": True, "messages": [{"role": "user", "content": "hello"}]},
    ) as response:
        body = response.read()

    # Headers were already sent (HTTP 200), so the failure is surfaced as a terminal SSE
    # error event and the recorded status maps to 502.
    assert response.status_code == 200
    assert b"par" in body
    assert b"upstream_error" in body
    assert "upstream dropped mid-stream" not in body.decode("utf-8")
    assert '"status_code": 502' in caplog.text
    # The deferred recording maps the mid-stream failure to a 502 request metric.
    assert _requests_502() == before_502 + 1.0


def test_streaming_pre_first_byte_upstream_error_returns_502():
    class PreFirstByteFailingClient(FakeRuntimeClient):
        async def stream_chat_completions(self, payload, headers=None, backend=None):
            self.calls += 1
            self.payload = payload
            # Fail before yielding any bytes (e.g. response.raise_for_status()).
            raise httpx.HTTPStatusError(
                "runtime unavailable",
                request=httpx.Request("POST", "http://ollama:11434/v1/chat/completions"),
                response=httpx.Response(
                    503,
                    request=httpx.Request("POST", "http://ollama:11434/v1/chat/completions"),
                ),
            )
            yield b""  # pragma: no cover - unreachable, marks this an async generator

    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
        allow_streaming=True,
    )
    app = create_app(settings)
    app.state.runtime_client = PreFirstByteFailingClient()
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"stream": True, "messages": [{"role": "user", "content": "hello"}]},
    )

    # No bytes were sent yet, so a clean 502 JSON error is returned instead of a 200.
    assert response.status_code == 502
    assert response.json()["detail"]["runtime_status"] == 503


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
            "choices": [{"message": {"role": "assistant", "content": "hello from Ollama"}}],
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
    assert fake.payload["model"] == "default-model"


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
            "choices": [{"message": {"role": "assistant", "content": "traceable response"}}],
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


def test_health_and_readiness_probes_bypass_api_key_auth():
    # Kubernetes probes cannot present an API key; /healthz and /readyz must stay
    # reachable when API-key auth is enabled, or the gateway pod never goes Ready.
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
        allowed_models=("default-model",),
        api_key_auth_enabled=True,
        api_key_sha256s=(hashlib.sha256(b"secret-key").hexdigest(),),
    )
    app = create_app(settings)
    app.state.runtime_client = FakeRuntimeClient()
    client = TestClient(app)

    healthz = client.get("/healthz")
    readyz = client.get("/readyz")

    assert healthz.status_code == 200
    assert readyz.status_code == 200
    assert readyz.json()["status"] == "ready"


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


def test_jwt_bearer_token_is_accepted_when_enabled(monkeypatch):
    secret = b"jwt-test-secret"

    async def fake_keys(self):
        return [{"kty": "oct", "kid": "test-key", "k": _b64url(secret)}]

    monkeypatch.setattr(JwksCache, "keys", fake_keys)
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
        jwt_auth_enabled=True,
        jwt_jwks_url="https://issuer.example/.well-known/jwks.json",
        jwt_issuer="https://issuer.example",
        jwt_audience="private-ai-platform-kit",
        jwt_required_scopes=("chat:write",),
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
    token = _signed_hs256_jwt(
        secret,
        {
            "iss": "https://issuer.example",
            "aud": "private-ai-platform-kit",
            "scope": "chat:write tenant:read",
            "exp": int(time.time()) + 300,
        },
    )

    valid = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    invalid = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {_tamper_jwt_signature(token)}"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert valid.status_code == 200
    assert invalid.status_code == 401
    assert fake.calls == 1


def _hs256_jwt_settings(secret, **overrides):
    base = {
        "runtime_backend": "ollama",
        "ollama_base_url": "http://ollama:11434",
        "vllm_base_url": "http://vllm:8000",
        "model_id": "default-model",
        "request_timeout_seconds": 5,
        "jwt_auth_enabled": True,
        "jwt_jwks_url": "https://issuer.example/.well-known/jwks.json",
    }
    base.update(overrides)
    return Settings(**base)


def test_jwt_principal_is_recorded_in_audit(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="ai_platform_ops_lab.audit")
    secret = b"jwt-test-secret"

    async def fake_keys(self):
        return [{"kty": "oct", "kid": "test-key", "k": _b64url(secret)}]

    monkeypatch.setattr(JwksCache, "keys", fake_keys)
    app = create_app(_hs256_jwt_settings(secret))
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)
    token = _signed_hs256_jwt(
        secret,
        {"sub": "user-123", "azp": "agent-app", "scope": "chat:write", "exp": int(time.time()) + 300},
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    audit = [r.getMessage() for r in caplog.records if '"event": "inference_request"' in r.getMessage()][-1]
    event = json.loads(audit)
    assert event["principal"]["auth"] == "jwt"
    assert event["principal"]["sub"] == "user-123"
    assert event["principal"]["client_id"] == "agent-app"
    assert "chat:write" in event["principal"]["scopes"]


def test_jwt_tenant_claim_binds_sandbox(monkeypatch):
    secret = b"jwt-test-secret"

    async def fake_keys(self):
        return [{"kty": "oct", "kid": "test-key", "k": _b64url(secret)}]

    monkeypatch.setattr(JwksCache, "keys", fake_keys)
    app = create_app(_hs256_jwt_settings(secret, jwt_tenant_claim="sandbox"))
    fake = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    app.state.runtime_client = fake
    client = TestClient(app)
    token = _signed_hs256_jwt(secret, {"sandbox": "team-a", "exp": int(time.time()) + 300})

    # No header: sandbox is taken from the verified claim.
    no_header = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert no_header.status_code == 200
    assert no_header.headers["X-Sandbox-ID"] == "team-a"

    # Matching header is accepted.
    matching = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}", "X-Sandbox-ID": "team-a"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert matching.status_code == 200


def test_jwt_tenant_claim_mismatch_is_rejected(monkeypatch):
    secret = b"jwt-test-secret"

    async def fake_keys(self):
        return [{"kty": "oct", "kid": "test-key", "k": _b64url(secret)}]

    monkeypatch.setattr(JwksCache, "keys", fake_keys)
    app = create_app(_hs256_jwt_settings(secret, jwt_tenant_claim="sandbox"))
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)
    token = _signed_hs256_jwt(secret, {"sandbox": "team-a", "exp": int(time.time()) + 300})

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}", "X-Sandbox-ID": "team-b"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "sandbox_identity_mismatch"


def test_jwt_tenant_claim_missing_is_rejected(monkeypatch):
    secret = b"jwt-test-secret"

    async def fake_keys(self):
        return [{"kty": "oct", "kid": "test-key", "k": _b64url(secret)}]

    monkeypatch.setattr(JwksCache, "keys", fake_keys)
    app = create_app(_hs256_jwt_settings(secret, jwt_tenant_claim="sandbox"))
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)
    token = _signed_hs256_jwt(secret, {"exp": int(time.time()) + 300})

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "sandbox_claim_invalid"


def test_api_key_principal_is_recorded_in_audit(caplog):
    caplog.set_level(logging.INFO, logger="ai_platform_ops_lab.audit")
    api_key = "secret-key-value"
    # Precomputed sha256(api_key) hex; the gateway verifies api_key_sha256s by the same
    # digest. Hardcoded so the test does not hash a credential-named value.
    digest = "79bc72d042dbd44d111a583bfb0c58b696ed19d5f8c0f9165943aed5b1ddcb55"
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
        api_key_auth_enabled=True,
        api_key_sha256s=(digest,),
    )
    app = create_app(settings)
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    audit = [r.getMessage() for r in caplog.records if '"event": "inference_request"' in r.getMessage()][-1]
    event = json.loads(audit)
    assert event["principal"]["auth"] == "api_key"
    assert event["principal"]["key_id"] == digest[:12]


@pytest.mark.parametrize(
    ("algorithm", "key_factory", "jwk_factory", "signer"),
    [
        (
            "RS256",
            lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048),
            _rsa_jwk,
            _signed_rs256_jwt,
        ),
        (
            "ES256",
            lambda: ec.generate_private_key(ec.SECP256R1()),
            _ec_jwk,
            _signed_es256_jwt,
        ),
    ],
)
def test_oidc_jwks_asymmetric_jwt_is_accepted(monkeypatch, algorithm, key_factory, jwk_factory, signer):
    private_key = key_factory()

    async def fake_keys(self):
        return [jwk_factory(private_key)]

    monkeypatch.setattr(JwksCache, "keys", fake_keys)
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
        jwt_auth_enabled=True,
        jwt_jwks_url="https://issuer.example/.well-known/jwks.json",
        jwt_issuer="https://issuer.example",
        jwt_audience="private-ai-platform-kit",
        jwt_required_scopes=("chat:write",),
    )
    app = create_app(settings)
    fake = FakeRuntimeClient(
        response={
            "id": f"chatcmpl-{algorithm.lower()}",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        }
    )
    app.state.runtime_client = fake
    client = TestClient(app)
    token = signer(
        private_key,
        {
            "iss": "https://issuer.example",
            "aud": "private-ai-platform-kit",
            "scp": ["chat:write"],
            "exp": int(time.time()) + 300,
        },
    )

    valid = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"messages": [{"role": "user", "content": f"hello {algorithm}"}]},
    )
    invalid = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {_tamper_jwt_signature(token)}"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert valid.status_code == 200
    assert invalid.status_code == 401
    assert fake.calls == 1


def _jwt_only_settings():
    return Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="default-model",
        request_timeout_seconds=5,
        jwt_auth_enabled=True,
        jwt_jwks_url="https://issuer.example/.well-known/jwks.json",
    )


def test_jwks_unavailable_returns_503_not_401(monkeypatch):
    from app.jwt_auth import JwksUnavailableError

    async def unavailable(self):
        raise JwksUnavailableError("issuer unreachable")

    monkeypatch.setattr(JwksCache, "keys", unavailable)
    app = create_app(_jwt_only_settings())
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)
    token = _signed_hs256_jwt(b"any-secret", {"exp": int(time.time()) + 300})

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    # Issuer unreachable is retry-later (503), distinct from a rejected token (401).
    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "jwks_unavailable"
    assert response.headers["Retry-After"] == "5"


def test_invalid_token_returns_401_when_jwks_is_available(monkeypatch):
    async def fake_keys(self):
        return [{"kty": "oct", "kid": "test-key", "k": _b64url(b"real-secret")}]

    monkeypatch.setattr(JwksCache, "keys", fake_keys)
    app = create_app(_jwt_only_settings())
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)
    # Signed with the wrong secret: the issuer is reachable but the token is invalid.
    token = _signed_hs256_jwt(b"wrong-secret", {"exp": int(time.time()) + 300})

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["reason"] == "invalid_or_missing_api_key"


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
    assert response.json()["detail"]["reason"] == "model_not_allowed"
    assert "ModelRoutingPolicy" in response.json()["detail"]["message"]
    assert fake.payload is None


def test_model_routing_policy_routes_alias_to_configured_backend(tmp_path):
    policy_path = tmp_path / "model-routing.yaml"
    policy_path.write_text(
        """
apiVersion: platform.ai/v1alpha1
kind: ModelRoutingPolicy
spec:
  models:
    - id: qwen-coder
      backend: vllm
      aliases:
        - coder
    - id: qwen-local
      backend: ollama
""".strip(),
        encoding="utf-8",
    )
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="qwen-local",
        request_timeout_seconds=5,
        model_routing_policy_path=policy_path,
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
            "model": "coder",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    assert fake.backend == "vllm"
    assert fake.payload["model"] == "qwen-coder"
    assert [item["id"] for item in client.get("/v1/models").json()["data"]] == [
        "qwen-coder",
        "qwen-local",
    ]


def test_sandbox_policy_overrides_admission_and_budget(tmp_path):
    policy_path = tmp_path / "sandbox-policy.yaml"
    policy_path.write_text(
        """
apiVersion: platform.ai/v1alpha1
kind: SandboxPolicySet
spec:
  policies:
    - sandboxId: strict-lab
      allowedModels:
        - approved-model
      maxPromptChars: 20
      budgets:
        requestLimit: 1
""".strip(),
        encoding="utf-8",
    )
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="approved-model",
        request_timeout_seconds=5,
        allowed_models=("approved-model", "expensive-model"),
        max_prompt_chars=1000,
        sandbox_budget_enabled=True,
        sandbox_request_budget=99,
        sandbox_policy_path=policy_path,
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
    headers = {"X-Sandbox-ID": "strict-lab"}

    disallowed_model = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "expensive-model",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    too_large = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"messages": [{"role": "user", "content": "x" * 21}]},
    )
    first = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    second = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"messages": [{"role": "user", "content": "again"}]},
    )

    assert disallowed_model.status_code == 400
    assert disallowed_model.json()["detail"]["reason"] == "model_not_allowed"
    assert too_large.status_code == 400
    assert too_large.json()["detail"]["reason"] == "prompt_too_large"
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"]["reason"] == "sandbox_request_budget_exceeded"
    assert fake.calls == 1


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


def test_prompt_secret_detection_rejects_unquoted_api_key_assignment():
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://ollama:11434",
        vllm_base_url="http://vllm:8000",
        model_id="approved-model",
        request_timeout_seconds=5,
        allowed_models=("approved-model",),
        prompt_secret_detection_enabled=True,
        prompt_secret_patterns=("generic_api_key_assignment",),
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
                    "content": "A terminal printed API_KEY=EXAMPLE_SECRET_VALUE_1234567890abcdef.",
                }
            ]
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "prompt_secret_detected"
    assert "generic_api_key_assignment" in response.json()["detail"]["message"]
    assert "EXAMPLE_SECRET_VALUE" not in response.text
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
    assert second.status_code == 429
    assert second.headers["Retry-After"] == "86400"
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

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "86400"
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


def _retry_settings(**overrides):
    base = {
        "runtime_backend": "ollama",
        "ollama_base_url": "http://ollama:11434",
        "vllm_base_url": "http://vllm:8000",
        "model_id": "default-model",
        "request_timeout_seconds": 5,
        "runtime_max_retries": 1,
        "runtime_retry_backoff_seconds": 0.001,
    }
    base.update(overrides)
    return Settings(**base)


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


def test_request_id_rejects_control_characters():
    # Control bytes echoed into the X-Request-ID response header would be rejected
    # by the HTTP stack at write time (an unhandled 500), so validation must catch them.
    fake_request = types.SimpleNamespace(headers={"x-request-id": "bad\x01id"})

    with pytest.raises(ValueError, match="visible ASCII"):
        gateway_main._request_id_from_header(fake_request)
