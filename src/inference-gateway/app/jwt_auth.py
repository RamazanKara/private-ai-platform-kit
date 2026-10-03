"""JWT bearer authentication for the inference gateway.

The JWKS cache and signature verification are the shared core in ``app/jwks.py``, which is
byte-identical in both services (ADR 0015). This module adapts the gateway's settings to it
and adds the gateway-only check: required scopes.
"""

from __future__ import annotations

from typing import Any

from app.jwks import JwksCache as _CoreJwksCache
from app.jwks import (
    JwksUnavailableError,
    JwtAuthError,
    JwtConfig,
    JwtVerifierCore,
)
from app.settings import Settings

__all__ = ["JwksCache", "JwksUnavailableError", "JwtAuthError", "JwtVerifier", "jwt_config"]


def jwt_config(settings: Settings) -> JwtConfig:
    """Project the gateway's settings onto the shared JWT configuration."""
    return JwtConfig(
        enabled=settings.jwt_auth_enabled,
        jwks_url=settings.jwt_jwks_url,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        cache_seconds=settings.jwt_cache_seconds,
        request_timeout_seconds=settings.request_timeout_seconds,
    )


class JwksCache(_CoreJwksCache):
    """The shared JWKS cache, built from gateway settings."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(jwt_config(settings))
        self.settings = settings


class JwtVerifier(JwtVerifierCore):
    """Verify a bearer JWT and enforce the gateway's required scopes."""

    def __init__(self, settings: Settings, jwks_cache: Any | None = None) -> None:
        super().__init__(jwt_config(settings), jwks_cache or JwksCache(settings))
        self.settings = settings

    async def verify(self, token: str) -> dict[str, Any]:
        """Verify the token (shared core), then the required-scope policy."""
        claims = await super().verify(token)
        self._validate_scopes(claims)
        return claims

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
