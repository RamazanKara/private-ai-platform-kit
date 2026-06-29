import asyncio
import base64
import hashlib
import hmac
import json
import time

import httpx
import pytest
from app import jwt_auth
from app.jwt_auth import JwksCache, JwksUnavailableError, JwtAuthError, JwtVerifier
from app.settings import Settings

SECRET = b"super-secret-signing-key-0123456789"


def _verify(verifier: JwtVerifier, token: str) -> dict:
    return asyncio.run(verifier.verify(token))


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _hs256(claims: dict, kid: str = "test-key", secret: bytes = SECRET) -> str:
    header = {"alg": "HS256", "typ": "JWT", "kid": kid}
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_claims = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(secret, f"{encoded_header}.{encoded_claims}".encode("ascii"), hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_claims}.{_b64url(signature)}"


class _FakeJwks:
    def __init__(self, keys: list[dict]) -> None:
        self._keys = keys

    async def keys(self) -> list[dict]:
        return self._keys


def _oct_jwk(kid: str = "test-key", secret: bytes = SECRET) -> dict:
    return {"kty": "oct", "kid": kid, "use": "sig", "k": _b64url(secret)}


def _verifier(**overrides) -> JwtVerifier:
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://o:1",
        vllm_base_url="http://v:1",
        model_id="m",
        request_timeout_seconds=5,
        jwt_auth_enabled=True,
        jwt_jwks_url="https://idp.example/jwks",
        **overrides,
    )
    return JwtVerifier(settings, jwks_cache=_FakeJwks([_oct_jwk()]))


def _claims(**overrides) -> dict:
    base = {"exp": int(time.time()) + 3600, "iss": "https://idp.example", "aud": "platform", "scope": "read write"}
    base.update(overrides)
    return base


def test_verify_accepts_valid_token():
    verifier = _verifier(jwt_issuer="https://idp.example", jwt_audience="platform", jwt_required_scopes=("read",))
    assert _verify(verifier, _hs256(_claims()))["aud"] == "platform"


def test_verify_rejects_expired_token():
    with pytest.raises(JwtAuthError, match="expired or missing exp"):
        _verify(_verifier(), _hs256(_claims(exp=int(time.time()) - 10)))


def test_verify_rejects_not_yet_valid_token():
    with pytest.raises(JwtAuthError, match="not yet valid"):
        _verify(_verifier(), _hs256(_claims(nbf=int(time.time()) + 600)))


def test_verify_rejects_issuer_mismatch():
    with pytest.raises(JwtAuthError, match="issuer mismatch"):
        _verify(_verifier(jwt_issuer="https://idp.example"), _hs256(_claims(iss="https://evil")))


def test_verify_rejects_audience_mismatch():
    with pytest.raises(JwtAuthError, match="audience mismatch"):
        _verify(_verifier(jwt_audience="platform"), _hs256(_claims(aud="other")))


def test_verify_accepts_audience_list():
    verifier = _verifier(jwt_audience="platform")
    assert _verify(verifier, _hs256(_claims(aud=["other", "platform"])))


def test_verify_rejects_missing_scope():
    with pytest.raises(JwtAuthError, match="missing required scopes"):
        _verify(_verifier(jwt_required_scopes=("admin",)), _hs256(_claims(scope="read")))


def test_verify_rejects_malformed_token():
    with pytest.raises(JwtAuthError, match="three segments"):
        _verify(_verifier(), "only.two")


def test_verify_rejects_unsupported_algorithm():
    header = _b64url(json.dumps({"alg": "none", "kid": "test-key"}).encode("utf-8"))
    claims = _b64url(json.dumps(_claims()).encode("utf-8"))
    with pytest.raises(JwtAuthError, match="unsupported jwt alg"):
        _verify(_verifier(), f"{header}.{claims}.")


def test_verify_rejects_bad_signature():
    token = _hs256(_claims(), secret=b"a-different-secret-key-9876543210")
    with pytest.raises(JwtAuthError, match="signature verification failed"):
        _verify(_verifier(), token)


def test_verify_rejects_unknown_kid():
    with pytest.raises(JwtAuthError, match="oct key was not found"):
        _verify(_verifier(), _hs256(_claims(), kid="rotated-away"))


def test_verifier_follows_jwks_key_rotation():
    # Rotation drill: the issuer retires kid-old and publishes kid-new. After the JWKS cache
    # serves the new key, tokens signed with kid-new validate and retired-key tokens are rejected.
    new_secret = b"rotated-new-signing-key-abcdef012345"
    settings = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://o:1",
        vllm_base_url="http://v:1",
        model_id="m",
        request_timeout_seconds=5,
        jwt_auth_enabled=True,
        jwt_jwks_url="https://idp.example/jwks",
    )

    before = JwtVerifier(settings, jwks_cache=_FakeJwks([_oct_jwk("kid-old", SECRET)]))
    assert _verify(before, _hs256(_claims(), kid="kid-old", secret=SECRET))

    after = JwtVerifier(settings, jwks_cache=_FakeJwks([_oct_jwk("kid-new", new_secret)]))
    assert _verify(after, _hs256(_claims(), kid="kid-new", secret=new_secret))
    with pytest.raises(JwtAuthError, match="oct key was not found"):
        _verify(after, _hs256(_claims(), kid="kid-old", secret=SECRET))


def _jwks_settings(**overrides) -> Settings:
    base = {
        "runtime_backend": "ollama",
        "ollama_base_url": "http://o:1",
        "vllm_base_url": "http://v:1",
        "model_id": "m",
        "request_timeout_seconds": 5,
        "jwt_auth_enabled": True,
        "jwt_jwks_url": "https://idp.example/jwks",
    }
    base.update(overrides)
    return Settings(**base)


def _mock_async_client(monkeypatch, handler):
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        jwt_auth.httpx,
        "AsyncClient",
        lambda *args, **kwargs: real_async_client(transport=httpx.MockTransport(handler)),
    )


def test_jwks_cache_fetches_keys_over_async_http(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"keys": [_oct_jwk()]})

    _mock_async_client(monkeypatch, handler)
    cache = JwksCache(_jwks_settings())

    keys = asyncio.run(cache.keys())

    assert keys[0]["kid"] == "test-key"
    assert seen["url"] == "https://idp.example/jwks"


def test_jwks_cache_serves_last_known_good_on_fetch_failure(monkeypatch):
    state = {"fail": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            raise httpx.ConnectError("issuer down")
        return httpx.Response(200, json={"keys": [_oct_jwk()]})

    _mock_async_client(monkeypatch, handler)
    cache = JwksCache(_jwks_settings(jwt_cache_seconds=1))

    first = asyncio.run(cache.keys())
    assert first[0]["kid"] == "test-key"

    # Force the cached entry to expire, then make the next fetch fail.
    cache._expires_at = 0.0
    state["fail"] = True
    served = asyncio.run(cache.keys())

    # Last-known-good keys are served instead of rejecting valid tokens.
    assert served[0]["kid"] == "test-key"
    # A negative-cache backoff is applied so the issuer is not hammered per request.
    assert cache._negative_until > time.time()


def test_jwks_cache_raises_unavailable_when_no_keys_cached(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("issuer down")

    _mock_async_client(monkeypatch, handler)
    cache = JwksCache(_jwks_settings())

    with pytest.raises(JwksUnavailableError):
        asyncio.run(cache.keys())


def test_jwks_cache_returns_empty_when_auth_disabled():
    cache = JwksCache(_jwks_settings(jwt_auth_enabled=False, jwt_jwks_url=""))
    assert asyncio.run(cache.keys()) == []
