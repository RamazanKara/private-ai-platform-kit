"""JWT bearer authentication with JWKS-backed HS256, RS256, and ES256 verification."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from time import time
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from app.settings import Settings


class JwtAuthError(ValueError):
    """Raised when a JWT is malformed, unsupported, or fails verification."""


class JwksUnavailableError(RuntimeError):
    """Raised when JWKS keys cannot be obtained and no cached keys are available.

    Distinguished from :class:`JwtAuthError` so callers can return 503 (issuer
    unreachable, retry later) instead of 401 (token rejected).
    """


def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + pad).encode("ascii"))
    except ValueError as exc:
        # binascii.Error and UnicodeEncodeError both subclass ValueError. Mapping to
        # JwtAuthError keeps a garbage signature/key segment a 401 rejection instead
        # of an unhandled 500.
        raise JwtAuthError("jwt segment is not valid base64url") from exc


def _b64url_json(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(_b64url_decode(value))
    except Exception as exc:
        raise JwtAuthError("jwt segment is not valid base64url JSON") from exc
    if not isinstance(payload, dict):
        raise JwtAuthError("jwt segment must decode to an object")
    return payload


def _b64url_int(value: str) -> int:
    return int.from_bytes(_b64url_decode(value), "big")


# Cap the negative-cache backoff so a transient issuer outage is retried quickly
# while still shielding the issuer from a per-request thundering herd.
_MAX_JWKS_NEGATIVE_CACHE_SECONDS = 30.0


class JwksCache:
    """Fetch and time-cache the JWKS document from the configured issuer.

    Fetches run on ``httpx.AsyncClient`` so the event loop is never blocked. On a
    fetch failure the last-known-good keys are served (when present) and a short
    negative-cache backoff is applied to avoid hammering the issuer; when no keys
    have ever been cached the failure surfaces as :class:`JwksUnavailableError`.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._expires_at = 0.0
        self._negative_until = 0.0
        self._keys: list[dict[str, Any]] = []

    def _negative_cache_seconds(self) -> float:
        # Derived from the configured cache TTL (no new env contract): a small
        # fraction, capped, so retries stay frequent without flooding the issuer.
        return min(max(self.settings.jwt_cache_seconds / 10.0, 1.0), _MAX_JWKS_NEGATIVE_CACHE_SECONDS)

    async def keys(self) -> list[dict[str, Any]]:
        """Return cached JWKS keys, refreshing from the JWKS URL when expired.

        Serves last-known-good keys on a fetch failure (with a negative-cache
        backoff) and raises :class:`JwksUnavailableError` when no keys are cached.
        """
        if not self.settings.jwt_auth_enabled:
            return []
        now = time()
        if self._keys and now < self._expires_at:
            return self._keys
        if now < self._negative_until and (self._keys or not self.settings.jwt_jwks_url):
            return self._keys
        try:
            async with httpx.AsyncClient(timeout=min(self.settings.request_timeout_seconds, 10.0)) as client:
                response = await client.get(self.settings.jwt_jwks_url)
                response.raise_for_status()
                # A 200 with a non-JSON body (e.g. a proxy error page) is the same
                # operational failure as an unreachable issuer, not a token rejection;
                # ValueError covers json.JSONDecodeError.
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            self._negative_until = time() + self._negative_cache_seconds()
            if self._keys:
                # Serve last-known-good keys so a transient JWKS outage does not
                # reject otherwise-valid tokens.
                return self._keys
            raise JwksUnavailableError("JWKS document could not be fetched") from exc
        keys = payload.get("keys", []) if isinstance(payload, dict) else []
        if not isinstance(keys, list):
            raise JwtAuthError("JWKS response must contain a keys list")
        self._keys = [key for key in keys if isinstance(key, dict)]
        self._expires_at = time() + self.settings.jwt_cache_seconds
        self._negative_until = 0.0
        return self._keys


class JwtVerifier:
    """Verify JWT signatures and claims against JWKS keys and settings policy."""

    def __init__(self, settings: Settings, jwks_cache: JwksCache | None = None) -> None:
        self.settings = settings
        self.jwks_cache = jwks_cache or JwksCache(settings)

    async def verify(self, token: str) -> dict[str, Any]:
        """Verify the token signature and claims, returning the decoded claims.

        Awaits the JWKS cache (async HTTP fetch). Raises :class:`JwksUnavailableError`
        when keys cannot be obtained, distinct from a :class:`JwtAuthError` rejection.
        """
        parts = token.split(".")
        if len(parts) != 3:
            raise JwtAuthError("jwt must have three segments")
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        header = _b64url_json(parts[0])
        claims = _b64url_json(parts[1])
        algorithm = str(header.get("alg") or "")
        # Resolve the algorithm before fetching keys so an unsupported/none alg is
        # rejected as a 401 without ever touching the network (algorithm-confusion defense).
        if algorithm not in {"HS256", "RS256", "ES256"}:
            raise JwtAuthError("unsupported jwt alg; supported algorithms: HS256, RS256, ES256")
        jwks_keys = await self.jwks_cache.keys()
        if algorithm == "HS256":
            key = self._oct_key_for(header, jwks_keys)
            expected = hmac.new(key, signing_input, hashlib.sha256).digest()
            actual = _b64url_decode(parts[2])
            if not hmac.compare_digest(expected, actual):
                raise JwtAuthError("jwt signature verification failed")
        elif algorithm == "RS256":
            key = self._rsa_key_for(header, jwks_keys)
            self._verify_rsa(key, signing_input, parts[2])
        else:  # algorithm == "ES256"
            key = self._ec_key_for(header, jwks_keys)
            self._verify_ec(key, signing_input, parts[2])
        self._validate_claims(claims)
        return claims

    @staticmethod
    def _matching_keys(header: dict[str, Any], kty: str, jwks_keys: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return JWKS keys matching the header kid and the given key type."""
        kid = header.get("kid")
        keys: list[dict[str, Any]] = []
        for key in jwks_keys:
            if key.get("kty") != kty:
                continue
            if kid is not None and key.get("kid") != kid:
                continue
            if key.get("use") not in {None, "sig"}:
                continue
            keys.append(key)
        return keys

    def _oct_key_for(self, header: dict[str, Any], jwks_keys: list[dict[str, Any]]) -> bytes:
        """Return the symmetric (oct) key bytes for HS256 verification."""
        for key in self._matching_keys(header, "oct", jwks_keys):
            material = key.get("k")
            if not isinstance(material, str):
                continue
            return _b64url_decode(material)
        raise JwtAuthError("matching JWKS oct key was not found")

    def _rsa_key_for(self, header: dict[str, Any], jwks_keys: list[dict[str, Any]]):
        """Return the RSA public key for RS256 verification."""
        for key in self._matching_keys(header, "RSA", jwks_keys):
            if key.get("alg") not in {None, "RS256"}:
                continue
            n = key.get("n")
            e = key.get("e")
            if not isinstance(n, str) or not isinstance(e, str):
                continue
            public_numbers = rsa.RSAPublicNumbers(_b64url_int(e), _b64url_int(n))
            return public_numbers.public_key()
        raise JwtAuthError("matching JWKS RSA key was not found")

    def _ec_key_for(self, header: dict[str, Any], jwks_keys: list[dict[str, Any]]):
        """Return the P-256 elliptic-curve public key for ES256 verification."""
        for key in self._matching_keys(header, "EC", jwks_keys):
            if key.get("alg") not in {None, "ES256"}:
                continue
            if key.get("crv") != "P-256":
                continue
            x = key.get("x")
            y = key.get("y")
            if not isinstance(x, str) or not isinstance(y, str):
                continue
            public_numbers = ec.EllipticCurvePublicNumbers(
                _b64url_int(x),
                _b64url_int(y),
                ec.SECP256R1(),
            )
            return public_numbers.public_key()
        raise JwtAuthError("matching JWKS P-256 EC key was not found")

    @staticmethod
    def _verify_rsa(key: rsa.RSAPublicKey, signing_input: bytes, encoded_signature: str) -> None:
        try:
            key.verify(
                _b64url_decode(encoded_signature),
                signing_input,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except InvalidSignature as exc:
            raise JwtAuthError("jwt signature verification failed") from exc

    @staticmethod
    def _verify_ec(key: ec.EllipticCurvePublicKey, signing_input: bytes, encoded_signature: str) -> None:
        raw_signature = _b64url_decode(encoded_signature)
        if len(raw_signature) != 64:
            raise JwtAuthError("ES256 jwt signature must contain 64 raw signature bytes")
        r = int.from_bytes(raw_signature[:32], "big")
        s = int.from_bytes(raw_signature[32:], "big")
        der_signature = encode_dss_signature(r, s)
        try:
            key.verify(
                der_signature,
                signing_input,
                ec.ECDSA(hashes.SHA256()),
            )
        except InvalidSignature as exc:
            raise JwtAuthError("jwt signature verification failed") from exc

    def _validate_claims(self, claims: dict[str, Any]) -> None:
        """Validate expiry, not-before, issuer, audience, and required scopes."""
        now = int(time())
        exp = claims.get("exp")
        if not isinstance(exp, (int, float)) or exp <= now:
            raise JwtAuthError("jwt is expired or missing exp")
        nbf = claims.get("nbf")
        if isinstance(nbf, (int, float)) and nbf > now:
            raise JwtAuthError("jwt is not yet valid")
        issuer = self.settings.jwt_issuer
        if issuer and claims.get("iss") != issuer:
            raise JwtAuthError("jwt issuer mismatch")
        audience = self.settings.jwt_audience
        if audience:
            raw_audience = claims.get("aud")
            audiences = raw_audience if isinstance(raw_audience, list) else [raw_audience]
            if audience not in audiences:
                raise JwtAuthError("jwt audience mismatch")
        missing_scopes = sorted(set(self.settings.jwt_required_scopes) - self._claim_scopes(claims))
        if missing_scopes:
            raise JwtAuthError(f"jwt missing required scopes: {missing_scopes}")

    @staticmethod
    def _claim_scopes(claims: dict[str, Any]) -> set[str]:
        scopes: set[str] = set()
        for field in ("scope", "scp"):
            value = claims.get(field)
            if isinstance(value, str):
                scopes.update(item for item in value.split() if item)
            elif isinstance(value, list):
                scopes.update(str(item) for item in value if str(item))
        return scopes
