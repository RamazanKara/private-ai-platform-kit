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


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


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


class JwksCache:
    """Fetch and time-cache the JWKS document from the configured issuer."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._expires_at = 0.0
        self._keys: list[dict[str, Any]] = []

    def keys(self) -> list[dict[str, Any]]:
        """Return cached JWKS keys, refreshing from the JWKS URL when expired."""
        if not self.settings.jwt_auth_enabled:
            return []
        if self._keys and time() < self._expires_at:
            return self._keys
        with httpx.Client(timeout=min(self.settings.request_timeout_seconds, 10.0)) as client:
            response = client.get(self.settings.jwt_jwks_url)
            response.raise_for_status()
            payload = response.json()
        keys = payload.get("keys", []) if isinstance(payload, dict) else []
        if not isinstance(keys, list):
            raise JwtAuthError("JWKS response must contain a keys list")
        self._keys = [key for key in keys if isinstance(key, dict)]
        self._expires_at = time() + self.settings.jwt_cache_seconds
        return self._keys


class JwtVerifier:
    """Verify JWT signatures and claims against JWKS keys and settings policy."""

    def __init__(self, settings: Settings, jwks_cache: JwksCache | None = None) -> None:
        self.settings = settings
        self.jwks_cache = jwks_cache or JwksCache(settings)

    def verify(self, token: str) -> dict[str, Any]:
        """Verify the token signature and claims, returning the decoded claims."""
        parts = token.split(".")
        if len(parts) != 3:
            raise JwtAuthError("jwt must have three segments")
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        header = _b64url_json(parts[0])
        claims = _b64url_json(parts[1])
        algorithm = str(header.get("alg") or "")
        if algorithm == "HS256":
            key = self._oct_key_for(header)
            expected = hmac.new(key, signing_input, hashlib.sha256).digest()
            actual = _b64url_decode(parts[2])
            if not hmac.compare_digest(expected, actual):
                raise JwtAuthError("jwt signature verification failed")
        elif algorithm == "RS256":
            key = self._rsa_key_for(header)
            self._verify_rsa(key, signing_input, parts[2])
        elif algorithm == "ES256":
            key = self._ec_key_for(header)
            self._verify_ec(key, signing_input, parts[2])
        else:
            raise JwtAuthError("unsupported jwt alg; supported algorithms: HS256, RS256, ES256")
        self._validate_claims(claims)
        return claims

    def _matching_keys(self, header: dict[str, Any], kty: str) -> list[dict[str, Any]]:
        """Return JWKS keys matching the header kid and the given key type."""
        kid = header.get("kid")
        keys: list[dict[str, Any]] = []
        # JwksCache.keys() is a domain accessor returning the JWK list, not Mapping.keys().
        for key in self.jwks_cache.keys():  # noqa: SIM118
            if key.get("kty") != kty:
                continue
            if kid is not None and key.get("kid") != kid:
                continue
            if key.get("use") not in {None, "sig"}:
                continue
            keys.append(key)
        return keys

    def _oct_key_for(self, header: dict[str, Any]) -> bytes:
        """Return the symmetric (oct) key bytes for HS256 verification."""
        for key in self._matching_keys(header, "oct"):
            material = key.get("k")
            if not isinstance(material, str):
                continue
            return _b64url_decode(material)
        raise JwtAuthError("matching JWKS oct key was not found")

    def _rsa_key_for(self, header: dict[str, Any]):
        """Return the RSA public key for RS256 verification."""
        for key in self._matching_keys(header, "RSA"):
            if key.get("alg") not in {None, "RS256"}:
                continue
            n = key.get("n")
            e = key.get("e")
            if not isinstance(n, str) or not isinstance(e, str):
                continue
            public_numbers = rsa.RSAPublicNumbers(_b64url_int(e), _b64url_int(n))
            return public_numbers.public_key()
        raise JwtAuthError("matching JWKS RSA key was not found")

    def _ec_key_for(self, header: dict[str, Any]):
        """Return the P-256 elliptic-curve public key for ES256 verification."""
        for key in self._matching_keys(header, "EC"):
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
