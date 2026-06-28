import base64
import hashlib
import hmac
import json
import time

import pytest
from app.jwt_auth import JwtAuthError, JwtVerifier
from app.settings import Settings

SECRET = b"super-secret-signing-key-0123456789"


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

    def keys(self) -> list[dict]:
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
    assert verifier.verify(_hs256(_claims()))["aud"] == "platform"


def test_verify_rejects_expired_token():
    with pytest.raises(JwtAuthError, match="expired or missing exp"):
        _verifier().verify(_hs256(_claims(exp=int(time.time()) - 10)))


def test_verify_rejects_not_yet_valid_token():
    with pytest.raises(JwtAuthError, match="not yet valid"):
        _verifier().verify(_hs256(_claims(nbf=int(time.time()) + 600)))


def test_verify_rejects_issuer_mismatch():
    with pytest.raises(JwtAuthError, match="issuer mismatch"):
        _verifier(jwt_issuer="https://idp.example").verify(_hs256(_claims(iss="https://evil")))


def test_verify_rejects_audience_mismatch():
    with pytest.raises(JwtAuthError, match="audience mismatch"):
        _verifier(jwt_audience="platform").verify(_hs256(_claims(aud="other")))


def test_verify_accepts_audience_list():
    verifier = _verifier(jwt_audience="platform")
    assert verifier.verify(_hs256(_claims(aud=["other", "platform"])))


def test_verify_rejects_missing_scope():
    with pytest.raises(JwtAuthError, match="missing required scopes"):
        _verifier(jwt_required_scopes=("admin",)).verify(_hs256(_claims(scope="read")))


def test_verify_rejects_malformed_token():
    with pytest.raises(JwtAuthError, match="three segments"):
        _verifier().verify("only.two")


def test_verify_rejects_unsupported_algorithm():
    header = _b64url(json.dumps({"alg": "none", "kid": "test-key"}).encode("utf-8"))
    claims = _b64url(json.dumps(_claims()).encode("utf-8"))
    with pytest.raises(JwtAuthError, match="unsupported jwt alg"):
        _verifier().verify(f"{header}.{claims}.")


def test_verify_rejects_bad_signature():
    token = _hs256(_claims(), secret=b"a-different-secret-key-9876543210")
    with pytest.raises(JwtAuthError, match="signature verification failed"):
        _verifier().verify(token)


def test_verify_rejects_unknown_kid():
    with pytest.raises(JwtAuthError, match="oct key was not found"):
        _verifier().verify(_hs256(_claims(), kid="rotated-away"))


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
    assert before.verify(_hs256(_claims(), kid="kid-old", secret=SECRET))

    after = JwtVerifier(settings, jwks_cache=_FakeJwks([_oct_jwk("kid-new", new_secret)]))
    assert after.verify(_hs256(_claims(), kid="kid-new", secret=new_secret))
    with pytest.raises(JwtAuthError, match="oct key was not found"):
        after.verify(_hs256(_claims(), kid="kid-old", secret=SECRET))
