"""JWT bearer verification for the RAG service.

The RAG service verifies its own audience-bound token so the caller's tenant is derived from
a *verified* claim rather than the client-asserted ``X-Sandbox-ID`` header. The JWKS cache and
signature verification are the shared core in ``app/jwks.py``, byte-identical in both
services (ADR 0015); this module adapts the RAG settings to it and adds the RAG-only step:
reading the tenant claim.
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
    """Project the RAG service's settings onto the shared JWT configuration."""
    return JwtConfig(
        enabled=settings.jwt_enabled,
        jwks_url=settings.jwt_jwks_url,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        cache_seconds=settings.jwt_cache_seconds,
        request_timeout_seconds=settings.jwt_request_timeout_seconds,
    )


class JwksCache(_CoreJwksCache):
    """The shared JWKS cache, built from RAG settings."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(jwt_config(settings))
        self.settings = settings


class JwtVerifier(JwtVerifierCore):
    """Verify a bearer JWT and read the tenant it is bound to."""

    def __init__(self, settings: Settings, jwks_cache: Any | None = None) -> None:
        super().__init__(jwt_config(settings), jwks_cache or JwksCache(settings))
        self.settings = settings

    def tenant_from_claims(self, claims: dict[str, Any]) -> str:
        """Return the tenant/sandbox id carried by the configured tenant claim.

        Raises :class:`JwtAuthError` when the claim is missing or empty so a token that
        does not name a tenant is not authorized for any sandbox (the caller surfaces
        this as a 403 rather than silently falling back to the client header).
        """
        claim_name = self.settings.jwt_tenant_claim
        raw = claims.get(claim_name)
        if raw is None or not str(raw).strip():
            raise JwtAuthError(f"jwt is missing the tenant claim '{claim_name}'")
        return str(raw).strip()
