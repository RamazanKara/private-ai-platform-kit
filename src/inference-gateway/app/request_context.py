"""HTTP request context, authentication, and gateway error responses."""

from __future__ import annotations

import hashlib
import hmac
import re
from time import time
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.jwt_auth import JwtAuthError, JwtVerifier
from app.key_records import KeyRecord, KeyRecordSet
from app.metrics import AUTH_FAILURES, LOAD_SHED, RATE_LIMITED
from app.metrics import sandbox_label as _sandbox_label
from app.settings import Settings, validate_sandbox_id

TRACEPARENT_PATTERN = re.compile(r"^[\da-f]{2}-[\da-f]{32}-[\da-f]{16}-[\da-f]{2}$")

# ``rate_limit_error``). Statuses absent here fall back in ``_error_type``.
_ERROR_TYPE_BY_STATUS = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    409: "conflict_error",
    413: "invalid_request_error",
    422: "invalid_request_error",
    429: "rate_limit_error",
}


def _error_type(status: int) -> str:
    """Return the OpenAI ``error.type`` for a status: mapped, else 5xx→api_error/4xx-ish."""
    mapped = _ERROR_TYPE_BY_STATUS.get(status)
    if mapped is not None:
        return mapped
    return "api_error" if status >= 500 else "invalid_request_error"


def _error_envelope(status_code: int, detail: Any) -> dict[str, Any]:
    """Reshape a gateway error body into an OpenAI-style envelope, preserving ``detail``.

    Returns ``{"error": {message, type, code?, request_id?, sandbox_id?}, "detail": <detail>}``.
    The original ``detail`` payload is kept alongside for one release so existing consumers
    that read ``detail.reason`` keep working while callers migrate to the ``error`` object.
    When ``detail`` is a mapping the machine ``reason`` becomes ``error.code`` and the
    request/sandbox identifiers are copied up so an SDK sees them without reaching into
    ``detail``.
    """
    if isinstance(detail, dict):
        message = detail.get("message") or detail.get("reason") or "request failed"
        error: dict[str, Any] = {"message": str(message), "type": _error_type(status_code)}
        reason = detail.get("reason")
        if reason:
            error["code"] = reason
        for field in ("request_id", "sandbox_id"):
            value = detail.get(field)
            if value is not None:
                error[field] = value
    else:
        error = {"message": str(detail), "type": _error_type(status_code)}
    return {"error": error, "detail": detail}


def _request_id_from_header(request: Request) -> str:
    """Return a validated X-Request-ID header value, generating a UUID when absent."""
    request_id = request.headers.get("x-request-id", "").strip()
    if not request_id:
        return str(uuid4())
    # Visible ASCII only: the value is echoed into the X-Request-ID response header,
    # where control bytes would be rejected at write time (an unhandled 500).
    if len(request_id) > 128 or any(not (33 <= ord(char) <= 126) for char in request_id):
        raise ValueError("X-Request-ID must be 1-128 visible ASCII characters without spaces")
    return request_id


def _traceparent_from_header(request: Request) -> str | None:
    """Return the validated W3C ``traceparent`` header, or None when not provided."""
    traceparent = request.headers.get("traceparent")
    if traceparent is None:
        return None
    traceparent = traceparent.strip().lower()
    if not TRACEPARENT_PATTERN.fullmatch(traceparent):
        raise ValueError("traceparent must use W3C trace context format")
    return traceparent


def _runtime_headers(request: Request) -> dict[str, str]:
    """Build the trace-propagation headers forwarded to the runtime backend."""
    headers = {
        "X-Request-ID": request.state.request_id,
        "X-Sandbox-ID": request.state.sandbox_id,
    }
    if request.state.traceparent:
        headers["traceparent"] = request.state.traceparent
    baggage = request.headers.get("baggage")
    if baggage:
        headers["baggage"] = baggage
    return headers


def _auth_required(path: str) -> bool:
    """Return whether the given request path requires authentication."""
    # /readyz must stay unauthenticated alongside /healthz: it is the Kubernetes
    # readiness probe, and the kubelet cannot present an API key, so requiring
    # auth here makes the pod never become Ready when API-key auth is enabled.
    # /console/* is the static admin console (ADR 0013): the page is public HTML/JS; the API
    # calls it makes to /v1/* carry the operator's key and are governed like any other request.
    return path not in {"/healthz", "/readyz", "/metrics", "/docs", "/openapi.json"} and not path.startswith("/console")


def _install_openapi_contract(app: FastAPI, settings: Settings) -> None:
    """Attach bearer/API-key security schemes to the app's generated OpenAPI schema."""
    default_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = default_openapi()
        components = schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "description": "Bearer token accepted by gateway middleware when API key authentication is enabled.",
        }
        security_schemes["ApiKeyAuth"] = {
            "type": "apiKey",
            "in": "header",
            "name": settings.api_key_header,
            "description": "API key header accepted by gateway middleware when API key authentication is enabled.",
        }
        for path, operations in schema.get("paths", {}).items():
            if not _auth_required(path):
                continue
            for method, operation in operations.items():
                if method.lower() in {"get", "post", "put", "patch", "delete"}:
                    operation["security"] = [{"BearerAuth": []}, {"ApiKeyAuth": []}]
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


def _api_key_from_request(request: Request, settings: Settings) -> str | None:
    """Extract the API key from the bearer token or configured API-key header."""
    authorization = request.headers.get("authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            return token
    api_key = request.headers.get(settings.api_key_header)
    if api_key:
        return api_key.strip()
    return None


def _api_key_digest(request: Request, settings: Settings) -> str | None:
    """Return the lowercase hex SHA-256 of the presented API key, or None when absent."""
    api_key = _api_key_from_request(request, settings)
    if not api_key:
        return None
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _matches_flat_key(digest: str, settings: Settings) -> bool:
    """Return whether the digest matches a flat ``api_key_sha256s`` hash (constant time)."""
    matched = False
    for expected in settings.api_key_sha256s:
        # Compare every entry (no early return) so the match does not leak, via timing,
        # which flat hash matched or how many precede it.
        if hmac.compare_digest(digest, expected):
            matched = True
    return matched


class ApiKeyOutcome:
    """Result of resolving a presented API key against flat hashes and key records.

    ``valid`` is True when the key matched either a flat hash or a non-expired record.
    ``record`` is the matched :class:`KeyRecord` (records carry binding/scope/budget),
    or None for a flat-hash match. ``expired`` is True when the key matched a record
    whose expiry has passed - a distinct 401 reason from an unrecognized key.
    """

    __slots__ = ("expired", "record", "valid")

    def __init__(self, valid: bool, record: KeyRecord | None, expired: bool) -> None:
        self.valid = valid
        self.record = record
        self.expired = expired


def _resolve_api_key(request: Request, settings: Settings, record_set: KeyRecordSet) -> ApiKeyOutcome:
    """Resolve the presented API key against key records and flat hashes.

    Records take precedence over the flat allowlist: a digest that matches a record is
    always governed by that record's binding/scopes/expiry, even if the same digest is
    also flat-listed - so hardening a flat key by adding a binding record can never be
    silently voided by leaving the flat entry in place. A flat-only match authenticates
    as an unbound principal. An expired record is rejected (never fails open to unbound).
    The digest is computed once and compared in constant time by each source.
    """
    digest = _api_key_digest(request, settings)
    if digest is None:
        return ApiKeyOutcome(valid=False, record=None, expired=False)
    record = record_set.match(digest)
    if record is not None:
        if record.is_expired(time()):
            return ApiKeyOutcome(valid=False, record=record, expired=True)
        return ApiKeyOutcome(valid=True, record=record, expired=False)
    if _matches_flat_key(digest, settings):
        return ApiKeyOutcome(valid=True, record=None, expired=False)
    return ApiKeyOutcome(valid=False, record=None, expired=False)


async def _valid_jwt(request: Request, verifier: JwtVerifier) -> dict[str, Any] | None:
    """Return the verified JWT claims, or ``None`` when the token is absent/invalid.

    Returning the claims (rather than a bool) lets the caller propagate the
    authenticated principal into request state and the audit trail. Propagates
    :class:`JwksUnavailableError` so the caller can distinguish an unreachable
    issuer (503) from a rejected token (401).
    """
    authorization = request.headers.get("authorization", "").strip()
    if not authorization.lower().startswith("bearer "):
        return None
    token = authorization[7:].strip()
    if not token or token.count(".") != 2:
        return None
    try:
        return await verifier.verify(token)
    except (JwtAuthError, httpx.HTTPError):
        return None


def _api_key_principal(request: Request, settings: Settings, record: KeyRecord | None = None) -> dict[str, Any]:
    """Build a non-reversible audit principal for an API-key caller.

    The key itself is never logged. For a flat-hash key ``key_id`` is a stable digest
    prefix; for a matched :class:`KeyRecord` it is the record's ``name`` (else its own
    12-char digest prefix) and the record's scopes and sandbox binding are recorded so
    audit consumers can attribute the request to the specific issued key.
    """
    if record is not None:
        principal: dict[str, Any] = {"auth": "api_key", "key_id": record.key_id}
        if record.scopes:
            principal["scopes"] = sorted(record.scopes)
        if record.sandbox is not None:
            principal["bound_sandbox"] = record.sandbox
        return principal
    api_key = _api_key_from_request(request, settings) or ""
    # Non-reversible attribution identifier, not a security control and not password
    # storage: API keys are high-entropy random tokens, so a digest prefix is a stable
    # audit handle (usedforsecurity=False marks this a non-cryptographic-control use).
    digest = hashlib.sha256(api_key.encode("utf-8"), usedforsecurity=False).hexdigest()
    return {"auth": "api_key", "key_id": digest[:12]}


def _jwt_principal(claims: dict[str, Any]) -> dict[str, Any]:
    """Summarize verified JWT claims into the audit principal (no raw token data)."""
    principal: dict[str, Any] = {"auth": "jwt"}
    subject = claims.get("sub")
    if subject is not None:
        principal["sub"] = str(subject)
    client_id = claims.get("azp") or claims.get("client_id")
    if client_id is not None:
        principal["client_id"] = str(client_id)
    issuer = claims.get("iss")
    if issuer is not None:
        principal["issuer"] = str(issuer)
    scopes = JwtVerifier._claim_scopes(claims)
    if scopes:
        principal["scopes"] = sorted(scopes)
    return principal


def _bound_sandbox_id(claims: dict[str, Any], claim_name: str) -> str:
    """Return the sandbox id bound to a JWT tenant claim, raising on missing/invalid.

    Raising surfaces as a 403: when an operator opts into claim binding, a token
    that lacks the claim or carries a malformed value is not authorized for any
    sandbox rather than silently falling back to the client-supplied header.
    """
    raw = claims.get(claim_name)
    if raw is None or not str(raw).strip():
        raise ValueError(f"jwt is missing the tenant claim '{claim_name}'")
    return validate_sandbox_id(str(raw))


def _auth_failure_response(request: Request, reason: str) -> JSONResponse:
    """Record the failure metric and build the 401 response with trace headers."""
    AUTH_FAILURES.labels(request.url.path, reason).inc()
    response = JSONResponse(
        status_code=401,
        content=_error_envelope(
            401,
            {
                "message": "authentication required",
                "reason": reason,
                "request_id": request.state.request_id,
                "sandbox_id": request.state.sandbox_id,
            },
        ),
    )
    response.headers["WWW-Authenticate"] = "Bearer"
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Sandbox-ID"] = request.state.sandbox_id
    if request.state.traceparent:
        response.headers["traceparent"] = request.state.traceparent
    return response


def _jwks_unavailable_response(request: Request) -> JSONResponse:
    """Build a 503 response when the JWKS issuer is unreachable (not a token rejection)."""
    AUTH_FAILURES.labels(request.url.path, "jwks_unavailable").inc()
    response = JSONResponse(
        status_code=503,
        content=_error_envelope(
            503,
            {
                "message": "authentication is temporarily unavailable",
                "reason": "jwks_unavailable",
                "request_id": request.state.request_id,
                "sandbox_id": request.state.sandbox_id,
            },
        ),
        headers={"Retry-After": "5"},
    )
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Sandbox-ID"] = request.state.sandbox_id
    if request.state.traceparent:
        response.headers["traceparent"] = request.state.traceparent
    return response


def _overloaded_response(request: Request) -> JSONResponse:
    """Build a 503 response when the gateway concurrency limit is exceeded (load shed)."""
    LOAD_SHED.labels(request.url.path).inc()
    response = JSONResponse(
        status_code=503,
        content=_error_envelope(
            503,
            {
                "message": "gateway is at capacity; retry shortly",
                "reason": "concurrency_limit",
                "request_id": request.state.request_id,
                "sandbox_id": request.state.sandbox_id,
            },
        ),
        headers={"Retry-After": "1"},
    )
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Sandbox-ID"] = request.state.sandbox_id
    if request.state.traceparent:
        response.headers["traceparent"] = request.state.traceparent
    return response


def _rate_limit_backend_unavailable_response(request: Request) -> JSONResponse:
    """Build a 503 when the shared rate-limit backend (Redis) is unreachable.

    Mirrors the budget tracker's backend-outage contract: a governance-store outage
    is a retryable 503, never a silent fail-open or an unhandled 500.
    """
    response = JSONResponse(
        status_code=503,
        content=_error_envelope(
            503,
            {
                "message": "rate limit backend is unavailable; retry shortly",
                "reason": "rate_limit_backend_unavailable",
                "request_id": request.state.request_id,
                "sandbox_id": request.state.sandbox_id,
            },
        ),
        headers={"Retry-After": "5"},
    )
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Sandbox-ID"] = request.state.sandbox_id
    if request.state.traceparent:
        response.headers["traceparent"] = request.state.traceparent
    return response


def _rate_limited_response(request: Request, retry_after: int) -> JSONResponse:
    """Build a 429 response with Retry-After when the per-sandbox rate limit is hit."""
    RATE_LIMITED.labels(_sandbox_label(request.state.sandbox_id)).inc()
    response = JSONResponse(
        status_code=429,
        content=_error_envelope(
            429,
            {
                "message": "rate limit exceeded for this sandbox",
                "reason": "rate_limited",
                "request_id": request.state.request_id,
                "sandbox_id": request.state.sandbox_id,
            },
        ),
    )
    if retry_after > 0:
        response.headers["Retry-After"] = str(retry_after)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Sandbox-ID"] = request.state.sandbox_id
    if request.state.traceparent:
        response.headers["traceparent"] = request.state.traceparent
    return response


def _sandbox_binding_response(request: Request, reason: str) -> JSONResponse:
    """Build a 403 when a JWT tenant claim is missing/invalid or contradicts the header."""
    AUTH_FAILURES.labels(request.url.path, reason).inc()
    response = JSONResponse(
        status_code=403,
        content=_error_envelope(
            403,
            {
                "message": "sandbox identity is not authorized for this caller",
                "reason": reason,
                "request_id": request.state.request_id,
                "sandbox_id": request.state.sandbox_id,
            },
        ),
    )
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Sandbox-ID"] = request.state.sandbox_id
    if request.state.traceparent:
        response.headers["traceparent"] = request.state.traceparent
    return response
