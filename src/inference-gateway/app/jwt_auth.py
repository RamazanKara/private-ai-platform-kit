"""JWT bearer authentication with JWKS-backed HS256, RS256, and ES256 verification.

Signature and standard-claim verification is delegated to the maintained
`PyJWT <https://pyjwt.readthedocs.io/>`_ library (``jwt.decode``) rather than a
hand-rolled RSA/EC implementation, while the JWKS fetch, last-known-good cache,
503-vs-401 distinction, algorithm allowlist, and tenant/scope enforcement remain
owned here so the external contract is byte-for-byte unchanged.
"""

from __future__ import annotations

import base64
import json
from time import time
from typing import Any

import httpx
import jwt
from jwt import PyJWK
from jwt.exceptions import (
    ExpiredSignatureError,
    ImmatureSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    PyJWKError,
    PyJWTError,
)

from app.settings import Settings

# Algorithm allowlist. The verifying algorithm is always taken from this set (never
# from the untrusted token header), which is what closes the classic alg-confusion
# attack where an attacker swaps an RS256 header for HS256 and signs with the public key.
_SUPPORTED_ALGORITHMS = ("HS256", "RS256", "ES256")

# JWK key type expected for each allowed signing algorithm.
_ALG_TO_KTY = {"HS256": "oct", "RS256": "RSA", "ES256": "EC"}


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

        The signature check and the exp/nbf/iss/aud claim checks are performed by
        PyJWT (``jwt.decode``); the verifying algorithm is pinned to the configured
        allowlist (never read from the token header) and the required-scope check is
        applied here since PyJWT has no notion of it.
        """
        parts = token.split(".")
        if len(parts) != 3:
            raise JwtAuthError("jwt must have three segments")
        header = _b64url_json(parts[0])
        # Surface a base64url-garbage signature/claims segment as a 401 (not a PyJWT
        # DecodeError-with-different-wording) before ever touching the network.
        _b64url_json(parts[1])
        _b64url_decode(parts[2])
        algorithm = str(header.get("alg") or "")
        # Resolve the algorithm before fetching keys so an unsupported/none alg is
        # rejected as a 401 without ever touching the network (algorithm-confusion defense).
        if algorithm not in _SUPPORTED_ALGORITHMS:
            raise JwtAuthError("unsupported jwt alg; supported algorithms: HS256, RS256, ES256")
        jwks_keys = await self.jwks_cache.keys()
        key = self._verifying_key(algorithm, header, jwks_keys)
        claims = self._decode(token, key, algorithm)
        self._validate_scopes(claims)
        return claims

    def _decode(self, token: str, key: PyJWK, algorithm: str) -> dict[str, Any]:
        """Run ``jwt.decode`` and translate PyJWT failures to the gateway's contract.

        ``algorithms`` is pinned to the single configured algorithm resolved from the
        allowlist, so PyJWT never honors a token-header algorithm (alg-confusion defense).
        Issuer/audience/exp/nbf are enforced by PyJWT; each specific rejection is mapped
        back to the exact 401 message the gateway has always returned.
        """
        issuer = self.settings.jwt_issuer or None
        audience = self.settings.jwt_audience or None
        options = {
            "require": ["exp"],
            "verify_exp": True,
            "verify_nbf": True,
            "verify_iss": issuer is not None,
            "verify_aud": audience is not None,
            "verify_signature": True,
        }
        try:
            claims = jwt.decode(
                token,
                key,  # type: ignore[arg-type]  # PyJWK carries the verifying key material
                algorithms=[algorithm],
                audience=audience,
                issuer=issuer,
                options=options,
            )
        except ExpiredSignatureError as exc:
            raise JwtAuthError("jwt is expired or missing exp") from exc
        except ImmatureSignatureError as exc:
            raise JwtAuthError("jwt is not yet valid") from exc
        except InvalidIssuerError as exc:
            raise JwtAuthError("jwt issuer mismatch") from exc
        except InvalidAudienceError as exc:
            raise JwtAuthError("jwt audience mismatch") from exc
        except PyJWTError as exc:
            # DecodeError/InvalidSignatureError/MissingRequiredClaimError/... all land
            # here. A missing exp is reported as an expiry failure to match the legacy
            # message; everything else is a signature/format rejection.
            missing_exp = "exp" in str(exc).lower()
            message = "jwt is expired or missing exp" if missing_exp else "jwt signature verification failed"
            raise JwtAuthError(message) from exc
        if not isinstance(claims, dict):  # pragma: no cover - PyJWT always returns a dict here
            raise JwtAuthError("jwt payload must decode to an object")
        return claims

    def _verifying_key(self, algorithm: str, header: dict[str, Any], jwks_keys: list[dict[str, Any]]) -> PyJWK:
        """Select the matching JWK for the header and build a PyJWT verifying key.

        Key selection (kid/kty/use matching and the algorithm's key type) stays here so
        the exact "matching JWKS <type> key was not found" rejection and the JWKS-cache
        rotation semantics are preserved; PyJWK only turns the chosen JWK into key material.
        """
        kty = _ALG_TO_KTY[algorithm]
        jwk = self._select_jwk(algorithm, kty, header, jwks_keys)
        try:
            return PyJWK.from_dict(jwk, algorithm=algorithm)
        except (PyJWKError, PyJWTError, ValueError, KeyError, TypeError) as exc:
            # A JWK that matched by kid/kty but is structurally unusable (bad modulus,
            # wrong curve, missing field) is a rejected token, not a 500.
            raise JwtAuthError(f"matching JWKS {self._key_kind(algorithm)} key was not found") from exc

    def _select_jwk(
        self, algorithm: str, kty: str, header: dict[str, Any], jwks_keys: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Return the JWK dict matching the header kid and the algorithm's key type.

        Raises :class:`JwtAuthError` with the algorithm-specific "key was not found"
        message when no candidate matches, exactly as before.
        """
        for jwk in self._matching_keys(header, kty, jwks_keys):
            alg = jwk.get("alg")
            if alg not in (None, algorithm):
                continue
            if algorithm == "ES256" and jwk.get("crv") != "P-256":
                continue
            if self._jwk_has_material(jwk, kty):
                return jwk
        raise JwtAuthError(f"matching JWKS {self._key_kind(algorithm)} key was not found")

    @staticmethod
    def _jwk_has_material(jwk: dict[str, Any], kty: str) -> bool:
        """Return whether a matched JWK carries the string fields its key type requires."""
        if kty == "oct":
            return isinstance(jwk.get("k"), str)
        if kty == "RSA":
            return isinstance(jwk.get("n"), str) and isinstance(jwk.get("e"), str)
        # EC
        return isinstance(jwk.get("x"), str) and isinstance(jwk.get("y"), str)

    @staticmethod
    def _key_kind(algorithm: str) -> str:
        """Return the human key-type name used in the "key was not found" rejection."""
        return {"HS256": "oct", "RS256": "RSA", "ES256": "P-256 EC"}[algorithm]

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

    def _validate_scopes(self, claims: dict[str, Any]) -> None:
        """Enforce the required-scope policy (PyJWT does not model scopes)."""
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
