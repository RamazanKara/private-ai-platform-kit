import hashlib
import json
import logging
import time

import pytest
from app.jwt_auth import JwksCache
from app.main import create_app
from app.settings import Settings
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient

from tests.gateway_support import (
    FakeRuntimeClient,
    _b64url,
    _ec_jwk,
    _rsa_jwk,
    _signed_es256_jwt,
    _signed_hs256_jwt,
    _signed_rs256_jwt,
    _tamper_jwt_signature,
)


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


def test_jwt_tenant_binding_scopes_usage_and_budget_reads(monkeypatch):
    # 4.2: with JWT tenant binding active, the GET /v1/usage and /v1/sandbox/budget routes
    # cannot read another tenant via X-Sandbox-ID - the binding check runs in middleware for
    # every auth-required route, so a mismatched header is rejected and a missing one adopts
    # the bound sandbox.
    secret = b"jwt-test-secret"

    async def fake_keys(self):
        return [{"kty": "oct", "kid": "test-key", "k": _b64url(secret)}]

    monkeypatch.setattr(JwksCache, "keys", fake_keys)
    app = create_app(_hs256_jwt_settings(secret, jwt_tenant_claim="sandbox"))
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    client = TestClient(app)
    token = _signed_hs256_jwt(secret, {"sandbox": "team-a", "exp": int(time.time()) + 300})

    cross_usage = client.get("/v1/usage", headers={"Authorization": f"Bearer {token}", "X-Sandbox-ID": "team-b"})
    own_usage = client.get("/v1/usage", headers={"Authorization": f"Bearer {token}"})
    cross_budget = client.get(
        "/v1/sandbox/budget", headers={"Authorization": f"Bearer {token}", "X-Sandbox-ID": "team-b"}
    )

    assert cross_usage.status_code == 403
    assert cross_usage.json()["detail"]["reason"] == "sandbox_identity_mismatch"
    assert own_usage.status_code == 200
    assert own_usage.json()["sandbox_id"] == "team-a"
    assert cross_budget.status_code == 403


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
