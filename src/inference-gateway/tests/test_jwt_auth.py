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
from cryptography.hazmat.primitives import serialization

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


def test_verify_rejects_garbage_base64_signature_as_auth_error():
    # A signature segment that is not base64url must be a 401 rejection
    # (JwtAuthError), never an unhandled binascii error surfacing as a 500.
    header, claims, _ = _hs256(_claims()).split(".")
    with pytest.raises(JwtAuthError, match="base64url"):
        _verify(_verifier(), f"{header}.{claims}.!!!not-base64!!!")


def test_jwks_cache_treats_non_json_body_as_unavailable(monkeypatch):
    # A 200 from an intermediary error page (HTML, not JSON) is an issuer outage
    # (503 semantics), not a token rejection or an unhandled JSONDecodeError.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>bad gateway</html>")

    _mock_async_client(monkeypatch, handler)
    cache = JwksCache(_jwks_settings())

    with pytest.raises(JwksUnavailableError):
        asyncio.run(cache.keys())


# --- PyJWT-backed asymmetric (RS256/ES256) verification ---------------------------------
#
# The signature/claim core is delegated to PyJWT (``jwt.decode``). These tests prove the
# swap preserves behavior at the API boundary: the verifying algorithm is pinned to the
# configured allowlist (never the token header), so an alg-confusion attempt is rejected;
# a valid RS256 and ES256 token still verifies; and bad-signature / wrong-audience /
# wrong-issuer / expired tokens are all still 401 (JwtAuthError), while an unreachable
# JWKS with no cache is still a 503 (JwksUnavailableError), not a 401.

import jwt as _pyjwt  # noqa: E402  (grouped with the PyJWT-specific tests below)
from cryptography.hazmat.primitives.asymmetric import ec as _ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa as _rsa  # noqa: E402


def _int_b64(value: int) -> str:
    length = (value.bit_length() + 7) // 8
    return _b64url(value.to_bytes(length, "big"))


def _fixed_int_b64(value: int, length: int) -> str:
    return _b64url(value.to_bytes(length, "big"))


def test_fixed_int_b64_preserves_leading_zero_octets():
    encoded = _fixed_int_b64(1, 32)
    decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    assert decoded == b"\x00" * 31 + b"\x01"


def _rsa_keypair(kid: str = "rsa-key"):
    private_key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private_key.public_key().public_numbers()
    jwk = {"kty": "RSA", "kid": kid, "use": "sig", "n": _int_b64(numbers.n), "e": _int_b64(numbers.e)}
    return private_key, jwk


def _ec_keypair(kid: str = "ec-key"):
    private_key = _ec.generate_private_key(_ec.SECP256R1())
    numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "EC",
        "kid": kid,
        "use": "sig",
        "crv": "P-256",
        # RFC 7518 requires P-256 coordinates to be exactly 32 octets. Minimal
        # integer encoding randomly drops a leading zero for roughly 1/256 keys.
        "x": _fixed_int_b64(numbers.x, 32),
        "y": _fixed_int_b64(numbers.y, 32),
    }
    return private_key, jwk


def _asym_verifier(jwk: dict, **overrides) -> JwtVerifier:
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
    return JwtVerifier(settings, jwks_cache=_FakeJwks([jwk]))


def _rs256(private_key, claims: dict, kid: str = "rsa-key") -> str:
    return _pyjwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def _es256(private_key, claims: dict, kid: str = "ec-key") -> str:
    return _pyjwt.encode(claims, private_key, algorithm="ES256", headers={"kid": kid})


def test_verify_accepts_valid_rs256_token():
    private_key, jwk = _rsa_keypair()
    verifier = _asym_verifier(
        jwk, jwt_issuer="https://idp.example", jwt_audience="platform", jwt_required_scopes=("read",)
    )
    claims = _verify(verifier, _rs256(private_key, _claims()))
    assert claims["aud"] == "platform"


def test_verify_accepts_valid_es256_token():
    private_key, jwk = _ec_keypair()
    verifier = _asym_verifier(
        jwk, jwt_issuer="https://idp.example", jwt_audience="platform", jwt_required_scopes=("read",)
    )
    claims = _verify(verifier, _es256(private_key, _claims()))
    assert claims["aud"] == "platform"


def test_verify_rejects_alg_confusion_hs256_token_when_rs256_key_published():
    # Classic RS256->HS256 confusion: the attacker HMAC-signs the token with the RSA
    # public key material and sets alg=HS256, hoping the verifier trusts the header.
    # The gateway pins algorithms to the RSA key's type, so PyJWT refuses to treat the
    # asymmetric key as an HMAC secret -> 401, never an accepted forgery.
    private_key, jwk = _rsa_keypair()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "rsa-key"}).encode("utf-8"))
    payload = _b64url(json.dumps(_claims()).encode("utf-8"))
    signature = hmac.new(public_pem, f"{header}.{payload}".encode("ascii"), hashlib.sha256).digest()
    forged = f"{header}.{payload}.{_b64url(signature)}"
    with pytest.raises(JwtAuthError):
        _verify(_asym_verifier(jwk), forged)


def test_verify_rejects_key_type_mismatch_es256_token_against_rsa_key():
    # A key-type mismatch: an ES256 token presented while only an RSA JWK is published.
    # Key selection finds no EC key -> the algorithm-specific "key was not found" 401.
    ec_private, _ = _ec_keypair(kid="rsa-key")
    _, rsa_jwk = _rsa_keypair(kid="rsa-key")
    with pytest.raises(JwtAuthError, match="P-256 EC key was not found"):
        _verify(_asym_verifier(rsa_jwk), _es256(ec_private, _claims(), kid="rsa-key"))


def test_verify_rejects_rs256_bad_signature():
    _, jwk = _rsa_keypair()
    other_key, _ = _rsa_keypair()  # sign with a key that does not match the published JWK
    token = _rs256(other_key, _claims())
    with pytest.raises(JwtAuthError, match="signature verification failed"):
        _verify(_asym_verifier(jwk), token)


def test_verify_rejects_rs256_wrong_audience():
    private_key, jwk = _rsa_keypair()
    with pytest.raises(JwtAuthError, match="audience mismatch"):
        _verify(_asym_verifier(jwk, jwt_audience="platform"), _rs256(private_key, _claims(aud="other")))


def test_verify_rejects_rs256_wrong_issuer():
    private_key, jwk = _rsa_keypair()
    with pytest.raises(JwtAuthError, match="issuer mismatch"):
        _verify(_asym_verifier(jwk, jwt_issuer="https://idp.example"), _rs256(private_key, _claims(iss="https://evil")))


def test_verify_rejects_rs256_expired_token():
    private_key, jwk = _rsa_keypair()
    with pytest.raises(JwtAuthError, match="expired or missing exp"):
        _verify(_asym_verifier(jwk), _rs256(private_key, _claims(exp=int(time.time()) - 10)))


def test_verify_returns_503_not_401_when_jwks_unreachable_and_no_cache(monkeypatch):
    # A live JwksCache (not the fake) whose issuer is unreachable and which has never
    # cached a key must surface JwksUnavailableError (a 503 retry), never a JwtAuthError
    # 401 token rejection. This is the PyJWT-backed path's operational-vs-auth boundary.
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("issuer down")

    _mock_async_client(monkeypatch, handler)
    private_key, _ = _rsa_keypair()
    settings = _jwks_settings()
    verifier = JwtVerifier(settings, jwks_cache=JwksCache(settings))
    with pytest.raises(JwksUnavailableError):
        _verify(verifier, _rs256(private_key, _claims()))
