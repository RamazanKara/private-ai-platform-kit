"""OpenAI-compatible inference gateway with auth, admission, budgets, and runtime routing."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import re
from dataclasses import replace
from time import perf_counter, time
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.budget import (
    BudgetBackendError,
    BudgetReservation,
    SandboxBudgetTracker,
    build_sandbox_budget_tracker,
)
from app.cache import build_response_cache, cache_key
from app.jwt_auth import JwksUnavailableError, JwtAuthError, JwtVerifier
from app.key_records import KeyRecord, KeyRecordSet, key_record_effective_budget_updates
from app.messages import (
    MessagesRequest,
    anthropic_to_chat_payload,
    chat_completion_to_anthropic,
)
from app.policy import ModelRoutingPolicy, SandboxPolicySet
from app.ratelimit import build_rate_limiter
from app.responses import (
    ResponsesRequest,
    chat_completion_to_responses,
    responses_to_chat_payload,
)
from app.runtime_client import REDACTED_MESSAGE_FIELDS, RuntimeClient
from app.settings import (
    AdmissionPolicyError,
    Settings,
    completion_prompt_texts,
    extract_text_content,
    moderate_text,
    validate_sandbox_id,
)
from app.tracing import configure_tracing, trace_request

AUDIT_LOGGER = logging.getLogger("ai_platform_ops_lab.audit")
TRACEPARENT_PATTERN = re.compile(r"^[\da-f]{2}-[\da-f]{32}-[\da-f]{16}-[\da-f]{2}$")
# Tamper-evident audit chain: h_0 = SHA-256("genesis"); h_i = SHA-256(h_{i-1} ||
# canonical(record_i)). Matches paper/evidence-model/audit_chain.py so the live audit log
# is verifiable by the same auditor tooling. The chain is per-process (per gateway replica).
AUDIT_GENESIS = hashlib.sha256(b"genesis").hexdigest()
SERVICE_VERSION = "0.23.0"
OPENAPI_DESCRIPTION = (
    "OpenAI-compatible private inference gateway with sandbox traceability, "
    "admission controls, budget enforcement, redacted audit events, and "
    "runtime routing to Ollama or vLLM."
)
OPENAPI_TAGS = [
    {"name": "health", "description": "Readiness and liveness checks."},
    {"name": "observability", "description": "Prometheus metrics endpoints."},
    {"name": "sandbox", "description": "Sandbox-scoped budget and trace controls."},
    {"name": "inference", "description": "OpenAI-compatible inference endpoints."},
]

REQUESTS = Counter(
    "inference_gateway_requests_total",
    "Total inference gateway requests by route, backend, and status.",
    ["route", "backend", "status"],
)
LATENCY = Histogram(
    "inference_gateway_request_duration_seconds",
    "Inference gateway request latency by route and backend.",
    ["route", "backend"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)
SANDBOX_REQUESTS = Counter(
    "inference_gateway_sandbox_requests_total",
    "Total inference gateway requests by sandbox, backend, and status.",
    ["sandbox", "backend", "status"],
)
TOKEN_USAGE = Counter(
    "inference_gateway_tokens_total",
    "Runtime-reported token usage by backend and token type.",
    ["backend", "token_type"],
)
ADMISSION_REJECTIONS = Counter(
    "inference_gateway_admission_rejections_total",
    "Inference requests rejected by gateway admission policy.",
    ["reason", "backend", "sandbox"],
)
SANDBOX_BUDGET_USAGE = Gauge(
    "inference_gateway_sandbox_budget_usage",
    "Current sandbox budget usage by budget type.",
    ["sandbox", "budget_type"],
)
SANDBOX_BUDGET_LIMIT = Gauge(
    "inference_gateway_sandbox_budget_limit",
    "Configured sandbox budget limit by budget type. Zero means unlimited.",
    ["sandbox", "budget_type"],
)
AUTH_FAILURES = Counter(
    "inference_gateway_auth_failures_total",
    "Total gateway authentication failures by route and reason.",
    ["route", "reason"],
)
RATE_LIMITED = Counter(
    "inference_gateway_rate_limited_total",
    "Total gateway requests rejected by the per-sandbox rate limiter.",
    ["sandbox"],
)
RATE_LIMIT_FAIL_OPEN = Counter(
    "inference_gateway_rate_limit_fail_open_total",
    "Requests admitted without a rate-limit check because the backend was down and "
    "RATE_LIMIT_FAIL_OPEN is enabled (deliberate availability-over-enforcement fallback).",
    ["sandbox"],
)
RUNTIME_FALLBACKS = Counter(
    "inference_gateway_runtime_fallbacks_total",
    "Total times a request failed over from one runtime route to a fallback.",
    ["from_backend", "to_backend"],
)
LOAD_SHED = Counter(
    "inference_gateway_load_shed_total",
    "Total requests rejected by the gateway concurrency limit (load shedding).",
    ["route"],
)
INFLIGHT = Gauge(
    "inference_gateway_inflight_requests",
    "Current number of in-flight gateway requests subject to the concurrency limit.",
)
CACHE_LOOKUPS = Counter(
    "inference_gateway_response_cache_total",
    "Response cache lookups by result.",
    ["result"],
)
CANARY_ROUTED = Counter(
    "inference_gateway_canary_routed_total",
    "Requests routed to a canary model by weighted progressive delivery.",
    ["from_model", "to_model"],
)
SHADOW_REQUESTS = Counter(
    "inference_gateway_shadow_requests_total",
    "Shadow (mirrored) requests sent fire-and-forget to a shadow model.",
    ["backend", "result"],
)
OUTPUT_GUARDRAIL = Counter(
    "inference_gateway_output_guardrail_total",
    "Model completions acted on by the output guardrail, by action and surface.",
    ["action", "route"],
)
PROMPT_GUARDRAIL = Counter(
    "inference_gateway_prompt_guardrail_total",
    "Prompts acted on by the input secret guardrail in redact/flag mode, by action and surface.",
    ["action", "route"],
)
ESTIMATED_COST = Counter(
    "inference_gateway_estimated_cost_usd_total",
    "Estimated monetary cost of runtime usage by sandbox and backend (USD_PER_1K_TOKENS model).",
    ["sandbox", "backend"],
)

# Bound the distinct sandbox label values this process will emit. Sandbox ids are
# client-asserted (up to 63 free-form chars) unless JWT tenant binding is enabled, so
# without a bound a scripted client cycling X-Sandbox-ID values could mint unbounded
# Prometheus series. The cap comfortably exceeds a real tenant fleet; ids past it are
# still served and audited under their real id but collapse into one overflow label.
_MAX_SANDBOX_LABEL_VALUES = 2000
_SANDBOX_LABEL_VALUES: set[str] = set()
_SANDBOX_LABEL_OVERFLOW = "__other__"


def _sandbox_label(sandbox_id: str) -> str:
    """Return the sandbox metric label, collapsing past the cardinality bound."""
    if sandbox_id in _SANDBOX_LABEL_VALUES:
        return sandbox_id
    if len(_SANDBOX_LABEL_VALUES) < _MAX_SANDBOX_LABEL_VALUES:
        _SANDBOX_LABEL_VALUES.add(sandbox_id)
        return sandbox_id
    return _SANDBOX_LABEL_OVERFLOW


# OpenAI-shaped error taxonomy: map the HTTP status the gateway returns to the
# ``error.type`` string OpenAI SDKs branch on (e.g. ``openai.RateLimitError`` keys off
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


def _schedule_shadow(client: RuntimeClient, shadow_route: Any, payload_dict: dict[str, Any], request: Request) -> None:
    """Fire a mirrored request to the shadow model, discarding its response and errors.

    Runs as a detached task so it never adds latency to or fails the caller's request;
    used to evaluate a candidate model on real traffic before promotion.
    """
    shadow_payload = dict(payload_dict)
    shadow_payload["model"] = shadow_route.model_id
    shadow_payload.pop("stream", None)
    headers = _runtime_headers(request)

    async def _run() -> None:
        try:
            await client.chat_completions(shadow_payload, headers=headers, backend=shadow_route.backend)
            SHADOW_REQUESTS.labels(shadow_route.backend, "ok").inc()
        except Exception:
            SHADOW_REQUESTS.labels(shadow_route.backend, "error").inc()

    # Hold a strong reference until completion so the detached task is not GC'd mid-flight.
    tasks: set[asyncio.Task[None]] = request.app.state.background_tasks
    task = asyncio.ensure_future(_run())
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def _is_failover_worthy(exc: Exception) -> bool:
    """Return whether an upstream failure should trigger a fallback to the next route.

    Connection/transport errors and an open circuit always fail over; an HTTP status
    error fails over only for retryable server-side statuses (5xx/429), never a client
    error like 400/404 that the next runtime would also reject.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, httpx.HTTPError)


async def _open_stream_with_fallback(
    client: RuntimeClient,
    chain: list[Any],
    payload_dict: dict[str, Any],
    request: Request,
) -> tuple[Any, str, str, bytes | None]:
    """Open a chat stream, failing over to the next route on a pre-first-byte error.

    Returns the live stream generator, the backend and model id that served it, and the
    primed first chunk (``None`` for an empty stream). Once the first chunk is returned
    the response is committed; later failures are handled by the stream body itself.
    """
    last_exc: httpx.HTTPError | None = None
    for index, candidate in enumerate(chain):
        attempt = dict(payload_dict)
        attempt["model"] = candidate.model_id
        candidate_stream = client.stream_chat_completions(
            attempt,
            headers=_runtime_headers(request),
            backend=candidate.backend,
        )
        try:
            first_chunk = await candidate_stream.__anext__()
        except StopAsyncIteration:
            return candidate_stream, candidate.backend, candidate.model_id, None
        except httpx.HTTPError as exc:
            await candidate_stream.aclose()
            last_exc = exc
            if _is_failover_worthy(exc) and index + 1 < len(chain):
                RUNTIME_FALLBACKS.labels(candidate.backend, chain[index + 1].backend).inc()
                continue
            raise
        return candidate_stream, candidate.backend, candidate.model_id, first_chunk
    raise last_exc or RuntimeError("no runtime route available")


class Message(BaseModel):
    """A single chat message with an OpenAI-style role and content.

    ``content`` accepts a plain string, an OpenAI-style content-part array (text
    and ``image_url`` parts, enabling vision-capable runtimes), or ``null`` for an
    assistant turn that only carries ``tool_calls``. ``extra="allow"`` lets any
    additional OpenAI message fields pass through to the runtime unchanged.
    """

    model_config = ConfigDict(extra="allow")

    role: Literal["system", "developer", "user", "assistant", "tool", "function"]
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class ChatCompletionRequest(BaseModel):
    """Request body for an OpenAI-compatible chat completion call.

    Tool/function-calling and structured-output fields are modelled explicitly so
    they survive ``model_dump`` to the runtime (the flagship coding-agent path),
    and ``extra="allow"`` forwards any other OpenAI sampling parameter (``top_p``,
    ``stop``, ``seed``, ``stream_options``, ...) verbatim instead of silently
    dropping it.
    """

    model_config = ConfigDict(extra="allow")

    model: str | None = None
    messages: list[Message]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool | None = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    functions: list[dict[str, Any]] | None = None
    function_call: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None


class EmbeddingsRequest(BaseModel):
    """Request body for an OpenAI-compatible embeddings call.

    Routing embeddings through the gateway (rather than calling a separate embedding
    service directly) subjects them to the same auth, model allowlist, budget, and
    audit controls as chat completions. ``extra="allow"`` forwards provider params
    such as ``dimensions`` or ``encoding_format`` unchanged.
    """

    model_config = ConfigDict(extra="allow")

    model: str | None = None
    input: str | list[str]


class CompletionRequest(BaseModel):
    """Request body for an OpenAI-compatible legacy text completion call.

    The pre-chat ``/v1/completions`` API takes a ``prompt`` (a string or list of strings)
    rather than ``messages``. Routing it through the gateway subjects legacy-completion
    traffic to the same auth, allowlist, admission, budget, and audit controls as chat.
    ``extra="allow"`` forwards any other OpenAI sampling parameter to the runtime verbatim.
    """

    model_config = ConfigDict(extra="allow")

    model: str | None = None
    prompt: str | list[str]
    max_tokens: int | None = None
    stream: bool | None = False


class ModerationRequest(BaseModel):
    """Request body for an OpenAI-compatible moderations call."""

    model_config = ConfigDict(extra="allow")

    input: str | list[str]
    model: str | None = None


class BatchRequest(BaseModel):
    """A batch of chat-completion requests processed in one call.

    Each item runs through the same auth (the batch is one authenticated request), model
    allowlist, admission, and budget controls; items are processed concurrently and the
    response reports per-item success or error so one bad item does not fail the batch.
    """

    model_config = ConfigDict(extra="allow")

    requests: list[ChatCompletionRequest]


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
    return path not in {"/healthz", "/readyz", "/metrics", "/docs", "/openapi.json"}


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


def _chain_audit_event(request: Request, event: dict[str, Any]) -> None:
    """Link the audit event into the per-process tamper-evident hash chain in place.

    Computes ``record_hash = SHA-256(prev_hash || canonical(event))`` over the event
    before the chain fields are added, then stamps ``prev_hash`` and ``record_hash`` onto
    the event and advances the stored head. Any edit, insertion, deletion, or reordering
    of the emitted records breaks the chain and is detectable by the auditor tooling.
    """
    state = request.app.state
    prev = getattr(state, "audit_prev_hash", AUDIT_GENESIS)
    canonical = json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")
    record_hash = hashlib.sha256(prev.encode("ascii") + canonical).hexdigest()
    event["prev_hash"] = prev
    event["record_hash"] = record_hash
    state.audit_prev_hash = record_hash


def _payload_fingerprint(payload: dict[str, Any]) -> dict[str, Any]:
    """Summarize a chat payload into redacted audit fields (counts, roles, prompt hash)."""
    messages = payload.get("messages") or []
    # Embeddings carry ``input`` and legacy completions carry ``prompt`` (both str or
    # list-of-str) instead of ``messages``; fingerprint either the same redacted way.
    raw_text_field = payload.get("input")
    if raw_text_field is None:
        raw_text_field = payload.get("prompt")
    if not messages and raw_text_field is not None:
        texts = [str(item) for item in (raw_text_field if isinstance(raw_text_field, list) else [raw_text_field])]
        canonical = json.dumps(texts, sort_keys=True, separators=(",", ":"))
        return {
            "input_count": len(texts),
            "prompt_chars": sum(len(text) for text in texts),
            "prompt_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        }
    canonical_messages = []
    roles = []
    prompt_chars = 0
    tool_call_count = 0
    for message in messages:
        role = str(message.get("role", "unknown"))
        text = extract_text_content(message.get("content"))
        roles.append(role)
        prompt_chars += len(text)
        # Hash the raw content structure (string or content-part array) so vision
        # parts and tool fields are covered by the fingerprint, not just text.
        canonical_messages.append({"role": role, "content": message.get("content")})
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            tool_call_count += len(tool_calls)
    canonical = json.dumps(canonical_messages, sort_keys=True, separators=(",", ":"), default=str)
    fingerprint: dict[str, Any] = {
        "message_count": len(messages),
        "message_roles": roles,
        "prompt_chars": prompt_chars,
        "prompt_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }
    if payload.get("tools"):
        fingerprint["tool_count"] = len(payload["tools"])
    if tool_call_count:
        fingerprint["tool_call_count"] = tool_call_count
    return fingerprint


def _record_token_usage(backend: str, runtime_response: dict[str, Any] | None) -> None:
    usage = (runtime_response or {}).get("usage")
    if not isinstance(usage, dict):
        return
    for token_type in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(token_type)
        if isinstance(value, (int, float)) and value >= 0:
            TOKEN_USAGE.labels(backend, token_type).inc(value)


def _record_estimated_cost(settings: Settings, sandbox_id: str, backend: str, usage: dict[str, Any] | None) -> None:
    """Increment the estimated-cost counter from runtime token usage.

    Exposes the same USD_PER_1K_TOKENS cost model used by ``/v1/usage`` as a Prometheus
    series so per-sandbox/backend spend is visualizable (FinOps/chargeback) rather than
    only readable as an ad-hoc JSON field. A zero rate leaves the cost model off.
    """
    if settings.usd_per_1k_tokens <= 0 or not isinstance(usage, dict):
        return
    total_tokens = usage.get("total_tokens")
    if not isinstance(total_tokens, (int, float)) or total_tokens < 0:
        return
    cost = (total_tokens / 1000.0) * settings.usd_per_1k_tokens
    if cost > 0:
        ESTIMATED_COST.labels(_sandbox_label(sandbox_id), backend).inc(cost)


def _guardrail_choice_text(choice: dict[str, Any]) -> str:
    """Return the assistant message text of an OpenAI-style choice, or '' when absent."""
    message = choice.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    return extract_text_content(content)


def _apply_output_guardrail(
    response: dict[str, Any] | None,
    settings: Settings,
    route: str,
    request: Request,
) -> None:
    """Inspect the runtime completion and flag/redact/block per the output guardrail.

    Runs the configured credential/PII/blocked-term detectors on each choice's assistant
    text (OWASP LLM02 insecure output handling / LLM06 sensitive-information disclosure).
    ``flag`` records only; ``redact`` rewrites matched spans in place; ``block`` withholds
    the content and sets ``finish_reason=content_filter``. Mutates ``response`` in place so
    the redacted/blocked body is what gets cached, audited, and returned.
    """
    if not settings.output_guardrail_enabled or not isinstance(response, dict):
        return
    choices = response.get("choices")
    if not isinstance(choices, list):
        return
    action: str | None = None
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        text = _guardrail_choice_text(choice)
        if not text:
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        if settings.output_guardrail_mode == "redact":
            redacted, matched = settings.redact_output_text(text)
            if matched:
                message["content"] = redacted
                action = "redacted"
        else:
            patterns, terms = settings.output_findings(text)
            if patterns or terms:
                if settings.output_guardrail_mode == "block":
                    message["content"] = "[response withheld by output policy]"
                    choice["finish_reason"] = "content_filter"
                    action = "blocked"
                else:
                    action = action or "flagged"
    if action:
        OUTPUT_GUARDRAIL.labels(action, route).inc()
        request.state.output_guardrail_action = action


def _apply_completion_output_guardrail(
    response: dict[str, Any] | None,
    settings: Settings,
    route: str,
    request: Request,
) -> None:
    """Flag/redact/block leaked secrets in legacy-completion ``choices[].text`` in place.

    The chat guardrail keys off ``choice.message.content``; legacy completions put the
    generated text in ``choice.text`` instead, so this mirrors the same redact/flag/block
    modes over that field using the shared output-scanning primitives (OWASP LLM02/LLM06).
    """
    if not settings.output_guardrail_enabled or not isinstance(response, dict):
        return
    choices = response.get("choices")
    if not isinstance(choices, list):
        return
    action: str | None = None
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        text = choice.get("text")
        if not isinstance(text, str) or not text:
            continue
        if settings.output_guardrail_mode == "redact":
            redacted, matched = settings.redact_output_text(text)
            if matched:
                choice["text"] = redacted
                action = "redacted"
        else:
            patterns, terms = settings.output_findings(text)
            if patterns or terms:
                if settings.output_guardrail_mode == "block":
                    choice["text"] = "[response withheld by output policy]"
                    choice["finish_reason"] = "content_filter"
                    action = "blocked"
                else:
                    action = action or "flagged"
    if action:
        OUTPUT_GUARDRAIL.labels(action, route).inc()
        request.state.output_guardrail_action = action


def _apply_prompt_secret_mode(settings: Settings, payload: dict[str, Any], route: str) -> str | None:
    """Redact or flag prompt secrets (redact/flag modes); return the action taken or None.

    Block mode is enforced earlier in admission; this handles the non-rejecting modes so
    an agent reading a ``.env`` or a lockfile does not kill its own conversation while the
    credential is still kept out of the runtime call (redact) or recorded (flag). Returning
    the action (rather than writing request state directly) lets the batch path attribute it
    to the individual item instead of clobbering one shared per-request field.
    """
    matched = settings.apply_prompt_secret_mode(payload)
    if not matched:
        return None
    action = "redacted" if settings.prompt_secret_mode == "redact" else "flagged"
    PROMPT_GUARDRAIL.labels(action, route).inc()
    return action


def _usage_from_sse_chunk(chunk: bytes) -> dict[str, Any] | None:
    """Return the ``usage`` object from a terminal SSE chunk, or None when absent.

    OpenAI-compatible streams emit a final ``data:`` event carrying a ``usage``
    object (when usage reporting is enabled) before ``data: [DONE]``. Each chunk may
    contain several SSE events; scan them and return the last usage object found.

    Any JSON object with a ``usage`` member necessarily contains the literal bytes
    ``"usage"``, so chunks and lines without them are skipped without parsing rather
    than json-decoding every delta event on the event loop.
    """
    if b'"usage"' not in chunk:
        return None
    found: dict[str, Any] | None = None
    for line in chunk.split(b"\n"):
        line = line.strip()
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if not data or data == b"[DONE]" or b'"usage"' not in data:
            continue
        try:
            parsed = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("usage"), dict):
            found = parsed["usage"]
    return found


_STREAM_REASONING_MARKERS: tuple[bytes, ...] = tuple(f'"{field}"'.encode() for field in REDACTED_MESSAGE_FIELDS)


def _is_usage_only_event(obj: dict[str, Any]) -> bool:
    """True when an SSE chunk object carries a usage object but no completion choices.

    This is the shape of the terminal usage event vLLM emits under
    ``stream_options.include_usage``; a normal content chunk that also carries usage
    keeps its choices and is not matched, so real content is never dropped.
    """
    return isinstance(obj.get("usage"), dict) and not obj.get("choices")


def _strip_reasoning_delta(obj: dict[str, Any]) -> bool:
    """Remove reasoning/thinking fields from each choice's delta/message in place.

    Returns True when anything was removed. Mirrors the non-streaming
    ``sanitize_chat_completion`` redaction so chain-of-thought cannot leak through
    the streaming path that a caller reaches by setting ``stream: true``.
    """
    choices = obj.get("choices")
    if not isinstance(choices, list):
        return False
    changed = False
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        for container_key in ("delta", "message"):
            container = choice.get(container_key)
            if isinstance(container, dict):
                for field in REDACTED_MESSAGE_FIELDS:
                    if field in container:
                        del container[field]
                        changed = True
    return changed


def _rewrite_stream_segment(segment: bytes, *, drop_usage_only: bool, strip_reasoning: bool) -> bytes:
    """Filter a complete run of SSE text before it is forwarded to the client.

    ``drop_usage_only`` removes the synthetic usage-only event induced by injecting
    ``stream_options.include_usage`` when the caller did not request usage, so the
    client-facing stream matches what it asked for. ``strip_reasoning`` removes
    reasoning/thinking fields from streamed deltas. Lines triggering neither transform
    are passed through byte-for-byte, keeping the common delta path cheap and lossless.
    """
    need_usage = drop_usage_only and b'"usage"' in segment
    need_reasoning = strip_reasoning and any(marker in segment for marker in _STREAM_REASONING_MARKERS)
    if not need_usage and not need_reasoning:
        return segment
    out: list[bytes] = []
    # When a usage-only event is dropped, also swallow the blank line that terminated it so
    # removing the event does not leave a stray extra separator in the client stream.
    skip_blank = False
    for line in segment.split(b"\n"):
        stripped = line.strip()
        if skip_blank and not stripped:
            skip_blank = False
            continue
        skip_blank = False
        if stripped.startswith(b"data:"):
            data = stripped[5:].strip()
            if data and data != b"[DONE]":
                try:
                    obj = json.loads(data)
                except (ValueError, UnicodeDecodeError):
                    out.append(line)
                    continue
                if isinstance(obj, dict):
                    if need_usage and _is_usage_only_event(obj):
                        skip_blank = True
                        continue
                    if need_reasoning and _strip_reasoning_delta(obj):
                        out.append(b"data: " + json.dumps(obj, separators=(",", ":")).encode("utf-8"))
                        continue
        out.append(line)
    return b"\n".join(out)


def _terminal_stream_error_event(backend: str, request: Request) -> bytes:
    """Build a terminal SSE error event emitted when the upstream fails mid-stream."""
    payload = {
        "error": {
            "message": "runtime stream failed",
            "type": "upstream_error",
            "backend": backend,
            "request_id": request.state.request_id,
            "sandbox_id": request.state.sandbox_id,
        }
    }
    return b"data: " + json.dumps(payload).encode("utf-8") + b"\n\n"


def _record_budget_reservation(reservation: BudgetReservation | None, settings: Settings) -> None:
    if reservation is None:
        return
    limits = {
        "requests": settings.sandbox_request_budget,
        "prompt_chars": settings.sandbox_prompt_char_budget,
        "estimated_tokens": settings.sandbox_estimated_token_budget,
    }
    usage = {
        "requests": reservation.usage.requests,
        "prompt_chars": reservation.usage.prompt_chars,
        "estimated_tokens": reservation.usage.estimated_tokens,
    }
    for budget_type, value in usage.items():
        SANDBOX_BUDGET_USAGE.labels(_sandbox_label(reservation.sandbox_id), budget_type).set(value)
        SANDBOX_BUDGET_LIMIT.labels(_sandbox_label(reservation.sandbox_id), budget_type).set(limits[budget_type])


def _budget_headers(reservation: BudgetReservation | None, settings: Settings) -> dict[str, str]:
    """Build OpenAI-style ``x-ratelimit-*`` response headers from a budget reservation.

    Mirrors the OpenAI API's budget headers so agent frameworks that parse them can
    pace themselves against the sandbox budget. Remaining values are floored at zero;
    a limit of zero means unlimited and emits no headers for that dimension.
    """
    if reservation is None:
        return {}
    headers: dict[str, str] = {}
    if settings.sandbox_request_budget > 0:
        headers["x-ratelimit-limit-requests"] = str(settings.sandbox_request_budget)
        headers["x-ratelimit-remaining-requests"] = str(
            max(settings.sandbox_request_budget - reservation.usage.requests, 0)
        )
    if settings.sandbox_estimated_token_budget > 0:
        headers["x-ratelimit-limit-tokens"] = str(settings.sandbox_estimated_token_budget)
        headers["x-ratelimit-remaining-tokens"] = str(
            max(settings.sandbox_estimated_token_budget - reservation.usage.estimated_tokens, 0)
        )
    return headers


def _admission_status(reason: str, settings: Settings) -> tuple[int, dict[str, str] | None]:
    if reason.startswith("sandbox_") and reason.endswith("_exceeded"):
        headers = None
        if settings.sandbox_budget_window_seconds > 0:
            headers = {"Retry-After": str(settings.sandbox_budget_window_seconds)}
        return 429, headers
    return 400, None


def _write_audit_log(
    settings: Settings,
    request: Request,
    payload: dict[str, Any],
    status_code: int,
    latency_seconds: float,
    backend: str,
    runtime_response: dict[str, Any] | None = None,
    runtime_status_code: int | None = None,
    error: str | None = None,
) -> None:
    if not settings.audit_log_enabled:
        return
    # Agent-action receipt semantics (ADR 0009): every sandbox-bound request is an
    # action with an explicit decision, so the chain doubles as a receipt stream.
    # Denials (admission, budget, guardrail block) carry decision=denied with the
    # reason in `error`; the guardrail outcome is recorded even when allowed.
    event = {
        "event": "inference_request",
        # Per-process chain identity (hash-covered): lets the verifier group records into
        # independent per-replica chains and anchor each head. Pre-v0.23.0 events lack it.
        "chain_id": getattr(request.app.state, "audit_chain_id", None),
        "action_type": "model_call",
        "decision": "allowed" if status_code < 400 else "denied",
        "guardrail_action": getattr(request.state, "output_guardrail_action", None),
        "prompt_guardrail_action": getattr(request.state, "prompt_guardrail_action", None),
        "request_id": request.state.request_id,
        "traceparent": request.state.traceparent,
        "sandbox_id": request.state.sandbox_id,
        "principal": getattr(request.state, "principal", None),
        "backend": backend,
        "model": payload.get("model") or settings.model_id,
        "status_code": status_code,
        "runtime_status_code": runtime_status_code,
        "latency_ms": round(latency_seconds * 1000, 2),
        # Chain-covered wall-clock timestamp: keeping WHEN inside the hash chain means
        # rewriting event times is as detectable as rewriting the events themselves.
        "ts": time(),
        "usage": (runtime_response or {}).get("usage"),
        "error": error,
        "budget": getattr(request.state, "budget_reservation", None),
    }
    event.update(_payload_fingerprint(payload))
    _chain_audit_event(request, event)
    line = json.dumps(event, sort_keys=True)
    AUDIT_LOGGER.info(line)
    logging.getLogger("uvicorn.error").info(line)


def _effective_settings(request: Request, policy_set: SandboxPolicySet, settings: Settings) -> Settings:
    """Return the request's effective settings: sandbox-policy overrides, then per-key budgets.

    Composes the two override sources onto the base settings for the (already bound)
    sandbox: the SandboxPolicySet's per-sandbox admission/budget overrides first, then
    any per-key budget overrides carried by a matched API-key record. The per-key budget
    takes precedence for the three budget dimensions so a key's issued allowance is what
    the request is metered against - and what /v1/usage and /v1/sandbox/budget report.
    """
    effective = policy_set.effective_settings(settings, request.state.sandbox_id)
    key_updates = getattr(request.state, "key_budget_updates", None)
    if key_updates:
        effective = replace(effective, **key_updates)
    return effective


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    app = FastAPI(
        title="Private AI Platform Kit Inference Gateway",
        version=SERVICE_VERSION,
        description=OPENAPI_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.settings = resolved
    app.state.runtime_client = RuntimeClient(resolved)
    app.router.add_event_handler("shutdown", app.state.runtime_client.aclose)
    app.state.budget_tracker = build_sandbox_budget_tracker(resolved)
    app.state.rate_limiter = build_rate_limiter(resolved)
    app.state.inflight = 0
    app.state.audit_prev_hash = AUDIT_GENESIS
    # Per-process audit chain identity: pod identity + process start time. It is stamped
    # onto every audit event (hash-covered) so the operator verifier can group records into
    # independent per-replica chains and anchor each head, instead of guessing chain
    # boundaries from genesis restarts in interleaved multi-replica logs.
    app.state.audit_chain_id = f"{os.getenv('HOSTNAME', 'gateway')}:{int(time())}"
    app.state.background_tasks = set()
    app.state.response_cache = build_response_cache(resolved)
    app.state.model_routing_policy = (
        ModelRoutingPolicy.from_path(resolved.model_routing_policy_path, resolved)
        if resolved.model_routing_policy_path
        else ModelRoutingPolicy.default(resolved)
    )
    app.state.sandbox_policy_set = SandboxPolicySet.from_path(resolved.sandbox_policy_path)
    # Optional richer API-key records (scopes/expiry/sandbox binding/budget). Fails closed:
    # a malformed key store raises here and stops startup rather than silently disabling
    # per-key controls. No records file -> an empty set, preserving flat-hash behavior.
    app.state.key_record_set = KeyRecordSet.from_path(resolved.api_key_records_path)
    app.state.jwt_verifier = JwtVerifier(resolved)
    tracing = configure_tracing(resolved)
    app.state.tracer = tracing[0] if tracing else None
    app.state.tracer_provider = tracing[1] if tracing else None
    if app.state.tracer_provider is not None:
        # Flush buffered spans on termination: BatchSpanProcessor otherwise drops its
        # queued tail every time a pod stops, losing the last requests' traces.
        app.router.add_event_handler("shutdown", app.state.tracer_provider.shutdown)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        try:
            request.state.request_id = _request_id_from_header(request)
            request.state.sandbox_id = validate_sandbox_id(
                request.headers.get("x-sandbox-id", resolved.default_sandbox_id)
            )
            request.state.traceparent = _traceparent_from_header(request)
            request.state.principal = None
            # Per-key budget overrides (from a matched API-key record) folded into the
            # request's effective settings; empty for flat keys and unauthenticated paths.
            request.state.key_budget_updates = {}
        except ValueError as exc:
            # A request header failed validation. The message is a controlled, developer-authored
            # validation string describing the malformed header (e.g. the sandbox-id charset rule) —
            # it carries no stack trace or server internals, so returning it as an actionable 400 is
            # correct API behavior, not information disclosure. Also logged for server-side triage.
            reason = str(exc)
            logging.getLogger("uvicorn.error").warning("rejected malformed request header: %s", reason)
            return JSONResponse(status_code=400, content={"detail": reason})

        async def dispatch() -> Response:
            if (resolved.api_key_auth_enabled or resolved.jwt_auth_enabled) and _auth_required(request.url.path):
                api_key_outcome = (
                    _resolve_api_key(request, resolved, request.app.state.key_record_set)
                    if resolved.api_key_auth_enabled
                    else ApiKeyOutcome(valid=False, record=None, expired=False)
                )
                jwt_claims: dict[str, Any] | None = None
                if not api_key_outcome.valid and resolved.jwt_auth_enabled:
                    try:
                        jwt_claims = await _valid_jwt(request, request.app.state.jwt_verifier)
                    except JwksUnavailableError:
                        # Issuer JWKS is unreachable: this is a 503 (retry later),
                        # not a 401 token rejection.
                        return _jwks_unavailable_response(request)
                if not api_key_outcome.valid and jwt_claims is None:
                    # A presented key that matched a record but is expired is a distinct,
                    # more actionable rejection than an unrecognized key - never fall
                    # through to accepting it as unbound.
                    reason = "api_key_expired" if api_key_outcome.expired else "invalid_or_missing_api_key"
                    return _auth_failure_response(request, reason)
                # Propagate the authenticated principal so the audit trail records who
                # called, not just the (client-asserted) sandbox header.
                if api_key_outcome.valid:
                    record = api_key_outcome.record
                    request.state.principal = _api_key_principal(request, resolved, record)
                    if record is not None:
                        # A record with a sandbox binding is enforced exactly like the JWT
                        # tenant claim: a mismatched X-Sandbox-ID is rejected; a missing one
                        # adopts the bound sandbox. This closes the cross-tenant read on
                        # /v1/usage and /v1/sandbox/budget for API-key callers.
                        if record.sandbox is not None:
                            explicit = request.headers.get("x-sandbox-id")
                            if explicit is not None and validate_sandbox_id(explicit) != record.sandbox:
                                return _sandbox_binding_response(request, "sandbox_identity_mismatch")
                            request.state.sandbox_id = record.sandbox
                        # Fold per-key budget overrides into the request's effective settings
                        # via the same mechanism the sandbox policy set uses.
                        if record.has_budget_override():
                            request.state.key_budget_updates = key_record_effective_budget_updates(record)
                elif jwt_claims is not None:
                    request.state.principal = _jwt_principal(jwt_claims)
                    # Bind the sandbox to the verified tenant claim when configured,
                    # so per-sandbox budget/policy/attribution cannot be spoofed via
                    # the X-Sandbox-ID header.
                    if resolved.jwt_tenant_claim:
                        try:
                            bound = _bound_sandbox_id(jwt_claims, resolved.jwt_tenant_claim)
                        except ValueError:
                            return _sandbox_binding_response(request, "sandbox_claim_invalid")
                        explicit = request.headers.get("x-sandbox-id")
                        if explicit is not None and validate_sandbox_id(explicit) != bound:
                            return _sandbox_binding_response(request, "sandbox_identity_mismatch")
                        request.state.sandbox_id = bound

            # Short-window per-sandbox throttle (distinct from the cumulative budget):
            # bounds burst abuse. Checked after sandbox binding so the limit applies to
            # the authenticated tenant, not the spoofable header. The check runs on a
            # worker thread because the Redis client is synchronous; a slow (not down)
            # Redis must not stall the whole event loop.
            if resolved.rate_limit_enabled and _auth_required(request.url.path):
                try:
                    allowed, retry_after = await asyncio.to_thread(
                        request.app.state.rate_limiter.check, request.state.sandbox_id
                    )
                except BudgetBackendError:
                    # Deliberate availability-vs-enforcement tradeoff. Fail closed by default
                    # (503, matching the budget tracker); when RATE_LIMIT_FAIL_OPEN is set,
                    # admit the request instead so a Redis outage does not take down all
                    # traffic - recorded via a warning log and a metric so the degraded
                    # window is visible. Budgets are unaffected and stay fail-closed.
                    if not resolved.rate_limit_fail_open:
                        return _rate_limit_backend_unavailable_response(request)
                    RATE_LIMIT_FAIL_OPEN.labels(_sandbox_label(request.state.sandbox_id)).inc()
                    logging.getLogger("uvicorn.error").warning(
                        "rate limit backend unavailable; failing open (RATE_LIMIT_FAIL_OPEN=1) "
                        "for sandbox %s request %s",
                        request.state.sandbox_id,
                        request.state.request_id,
                    )
                else:
                    if not allowed:
                        return _rate_limited_response(request, retry_after)

            # Bounded concurrency with fast-fail load shedding: the check + increment is
            # synchronous (no await between), so it is atomic on the event loop. Excess
            # load is rejected with 503 rather than queued behind the httpx pool.
            limited = resolved.max_concurrent_requests > 0 and _auth_required(request.url.path)
            if limited:
                if request.app.state.inflight >= resolved.max_concurrent_requests:
                    return _overloaded_response(request)
                request.app.state.inflight += 1
                INFLIGHT.set(request.app.state.inflight)
            try:
                response = await call_next(request)
            except BaseException:
                if limited:
                    request.app.state.inflight -= 1
                    INFLIGHT.set(request.app.state.inflight)
                raise
            if limited:
                # Hold the concurrency slot until the response BODY completes, not just
                # the headers: for a streaming response the expensive runtime work happens
                # while the body is on the wire, so releasing at headers time would let
                # unbounded concurrent streams pile up behind a "bounded" gateway.
                body_iterator = getattr(response, "body_iterator", None)
                if body_iterator is None:
                    request.app.state.inflight -= 1
                    INFLIGHT.set(request.app.state.inflight)
                else:

                    async def _release_when_body_done(iterator: Any = body_iterator) -> Any:
                        try:
                            async for chunk in iterator:
                                yield chunk
                        finally:
                            request.app.state.inflight -= 1
                            INFLIGHT.set(request.app.state.inflight)

                    response.body_iterator = _release_when_body_done()
            response.headers["X-Request-ID"] = request.state.request_id
            response.headers["X-Sandbox-ID"] = request.state.sandbox_id
            if request.state.traceparent:
                response.headers["traceparent"] = request.state.traceparent
            if getattr(request.state, "cache_status", None):
                response.headers["X-Cache"] = request.state.cache_status
            for header, value in getattr(request.state, "budget_headers", {}).items():
                response.headers[header] = value
            if getattr(request.state, "output_guardrail_action", None):
                response.headers["X-Output-Guardrail"] = request.state.output_guardrail_action
            if getattr(request.state, "prompt_guardrail_action", None):
                response.headers["X-Prompt-Guardrail"] = request.state.prompt_guardrail_action
            return response

        tracer = request.app.state.tracer
        if tracer is None:
            return await dispatch()
        return await trace_request(tracer, request, dispatch)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Reshape every raised HTTPException - and FastAPI's own 404/405 - into the
        # OpenAI-style error envelope while preserving the handler's headers (WWW-Authenticate,
        # Retry-After, ...). Pydantic 422 validation errors go through FastAPI's separate
        # RequestValidationError handler and are intentionally left in their default shape.
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_envelope(exc.status_code, exc.detail),
            headers=getattr(exc, "headers", None),
        )

    @app.get(
        "/healthz",
        tags=["health"],
        summary="Check gateway readiness",
        operation_id="getGatewayHealth",
    )
    async def healthz() -> dict[str, str]:
        REQUESTS.labels("/healthz", resolved.runtime_backend, "200").inc()
        return {
            "status": "ok",
            "backend": resolved.runtime_backend,
            "model": resolved.model_id,
        }

    @app.get(
        "/readyz",
        tags=["health"],
        summary="Check gateway and configured runtime readiness",
        operation_id="getGatewayReadiness",
    )
    async def readyz(response: Response) -> dict[str, Any]:
        client: RuntimeClient = app.state.runtime_client
        policy: ModelRoutingPolicy = app.state.model_routing_policy
        backends = sorted({route.backend for route in policy.routes} or {resolved.runtime_backend})
        runtime_status: dict[str, Any] = {}
        ready = True
        for backend in backends:
            try:
                runtime_health = await client.health(backend)
                runtime_status[backend] = {
                    "status": "ok",
                    "detail": runtime_health.get("status", "ok"),
                }
            except Exception:
                ready = False
                runtime_status[backend] = {"status": "unavailable"}
        response.status_code = 200 if ready else 503
        REQUESTS.labels("/readyz", resolved.runtime_backend, str(response.status_code)).inc()
        return {
            "status": "ready" if ready else "not_ready",
            "models": policy.model_ids(),
            "runtimes": runtime_status,
        }

    @app.get(
        "/metrics",
        tags=["observability"],
        summary="Export Prometheus metrics",
        operation_id="getGatewayMetrics",
    )
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get(
        "/v1/sandbox/budget",
        tags=["sandbox"],
        summary="Get sandbox budget usage",
        operation_id="getSandboxBudget",
    )
    async def sandbox_budget(request: Request) -> dict[str, Any]:
        # request.state.sandbox_id is the caller's bound sandbox when a principal is bound
        # (JWT tenant claim or an API-key record with a sandbox): the auth middleware rejects
        # a mismatched X-Sandbox-ID and rebinds a missing one upstream, so a bound caller can
        # only ever read its own budget here. In header-trusted mode (no binding) the header is
        # honored as-is - the documented insecure default.
        tracker: SandboxBudgetTracker = request.app.state.budget_tracker
        policy_set: SandboxPolicySet = request.app.state.sandbox_policy_set
        effective = _effective_settings(request, policy_set, resolved)
        try:
            return await asyncio.to_thread(tracker.snapshot, request.state.sandbox_id, effective)
        except BudgetBackendError as exc:
            raise HTTPException(
                status_code=503,
                detail={"message": "sandbox budget backend is unavailable", "reason": "budget_backend_unavailable"},
                headers={"Retry-After": "5"},
            ) from exc

    @app.get(
        "/v1/usage",
        tags=["sandbox"],
        summary="Get sandbox usage and estimated cost",
        operation_id="getSandboxUsage",
    )
    async def sandbox_usage(request: Request) -> dict[str, Any]:
        # Per-sandbox usage plus an estimated monetary cost (the data layer an admin/usage
        # console renders). USD_PER_1K_TOKENS of 0 leaves the cost model off (cost = 0).
        # Like /v1/sandbox/budget, this reflects only request.state.sandbox_id, which the auth
        # middleware forces to the caller's bound sandbox for a bound principal (JWT tenant
        # claim or an API-key record with a sandbox) - a bound caller cannot read another
        # tenant's usage via X-Sandbox-ID. Header-trusted mode (no binding) is unchanged.
        tracker: SandboxBudgetTracker = request.app.state.budget_tracker
        policy_set: SandboxPolicySet = request.app.state.sandbox_policy_set
        effective = _effective_settings(request, policy_set, resolved)
        try:
            snapshot = await asyncio.to_thread(tracker.snapshot, request.state.sandbox_id, effective)
        except BudgetBackendError as exc:
            raise HTTPException(
                status_code=503,
                detail={"message": "sandbox budget backend is unavailable", "reason": "budget_backend_unavailable"},
                headers={"Retry-After": "5"},
            ) from exc
        usage = snapshot.get("usage") or {}
        estimated_tokens = usage.get("estimated_tokens", 0) if isinstance(usage, dict) else 0
        estimated_cost = round((estimated_tokens / 1000.0) * resolved.usd_per_1k_tokens, 6)
        return {
            "sandbox_id": request.state.sandbox_id,
            "usage": usage,
            "limits": snapshot.get("limits"),
            "estimated_cost": estimated_cost,
            "currency": resolved.cost_currency,
            "usd_per_1k_tokens": resolved.usd_per_1k_tokens,
        }

    @app.get(
        "/v1/models",
        tags=["inference"],
        summary="List approved private models",
        operation_id="listModels",
    )
    async def models() -> dict[str, Any]:
        REQUESTS.labels("/v1/models", resolved.runtime_backend, "200").inc()
        policy: ModelRoutingPolicy = app.state.model_routing_policy
        return {"object": "list", "data": policy.openai_models()}

    @app.post(
        "/v1/chat/completions",
        tags=["inference"],
        summary="Create a private chat completion",
        operation_id="createChatCompletion",
    )
    async def chat_completions(request: Request, payload: ChatCompletionRequest) -> dict[str, Any]:
        route = "/v1/chat/completions"
        backend = resolved.runtime_backend
        start = perf_counter()
        status = "200"
        status_code = 200
        runtime_status_code = None
        runtime_response = None
        error = None
        payload_dict = payload.model_dump(exclude_none=True)
        request.state.budget_reservation = None
        # When streaming, the response generator records metrics + audit at its own
        # end-of-stream; the outer finally must not double-record on the headers path.
        stream_owns_recording = False
        # A cache hit consumes no runtime tokens: the finally block must not re-count
        # the cached usage into the token/cost metrics (audit still records the hit).
        cache_hit = False
        try:
            policy: ModelRoutingPolicy = request.app.state.model_routing_policy
            sandbox_policies: SandboxPolicySet = request.app.state.sandbox_policy_set
            effective = _effective_settings(request, sandbox_policies, resolved)
            try:
                chain = policy.resolve_chain(payload_dict.get("model"), effective.model_id)
            except ValueError as exc:
                raise AdmissionPolicyError("model_not_allowed", str(exc)) from exc
            # Progressive delivery: resolve the shadow target from the originally-resolved
            # primary, then apply weighted canary selection (which may swap chain[0]).
            primary_route = chain[0]
            shadow_route = None if payload_dict.get("stream") else policy.shadow_target(primary_route)
            canary = policy.canary_target(primary_route, random.random())
            if canary.model_id != primary_route.model_id:
                CANARY_ROUTED.labels(primary_route.model_id, canary.model_id).inc()
                chain = [canary, *chain[1:]]
            model_route = chain[0]
            backend = model_route.backend
            payload_dict["model"] = model_route.model_id
            effective.validate_admission(payload_dict)
            # Redact/flag prompt secrets (non-block modes) before the payload is cached,
            # reserved, or sent - so a redacted credential is never persisted or forwarded.
            prompt_action = _apply_prompt_secret_mode(effective, payload_dict, route)
            if prompt_action:
                request.state.prompt_guardrail_action = prompt_action
            # Exact-match per-sandbox cache (non-streaming only). A hit returns the prior
            # response without a runtime call or budget reservation.
            cache_enabled = resolved.response_cache_enabled and not payload_dict.get("stream")
            cache_id = ""
            if cache_enabled:
                cache_id = cache_key(request.state.sandbox_id, payload_dict)
                cached = await asyncio.to_thread(request.app.state.response_cache.get, cache_id)
                if cached is not None:
                    CACHE_LOOKUPS.labels("hit").inc()
                    request.state.cache_status = "HIT"
                    cache_hit = True
                    # Bind for the finally block (audit), then return.
                    runtime_response = cached
                    return cached
                CACHE_LOOKUPS.labels("miss").inc()
                request.state.cache_status = "MISS"
            tracker: SandboxBudgetTracker = request.app.state.budget_tracker
            reservation = await asyncio.to_thread(tracker.reserve, request.state.sandbox_id, payload_dict, effective)
            request.state.budget_reservation = reservation.audit_dict() if reservation is not None else None
            _record_budget_reservation(reservation, effective)
            request.state.budget_headers = _budget_headers(reservation, effective)
            client: RuntimeClient = request.app.state.runtime_client
            if payload_dict.get("stream"):
                # Force the runtime to emit a terminal usage event so streamed traffic is
                # metered even when the caller forgot ``stream_options.include_usage``.
                # If the caller did not ask for usage, the induced event is filtered back
                # out below so the client-facing stream is unchanged.
                existing_stream_options = payload_dict.get("stream_options")
                has_stream_options = isinstance(existing_stream_options, dict)
                client_wants_usage = has_stream_options and bool(existing_stream_options.get("include_usage"))
                merged_stream_options = dict(existing_stream_options) if has_stream_options else {}
                merged_stream_options["include_usage"] = True
                payload_dict["stream_options"] = merged_stream_options
                # Open the stream with cross-runtime fallback: a pre-first-byte failure
                # on the primary route retries the next route in the chain. Once a byte
                # is yielded the response is committed and cannot fail over.
                stream, stream_backend, used_model, first_chunk = await _open_stream_with_fallback(
                    client, chain, payload_dict, request
                )
                backend = stream_backend
                payload_dict["model"] = used_model
                stream_owns_recording = True

                async def stream_body() -> Any:
                    stream_status = "200"
                    stream_status_code = 200
                    stream_error: str | None = None
                    usage: dict[str, Any] | None = None
                    # The streamed bytes are already committed to the wire, so the output
                    # guardrail cannot redact/block them mid-stream; instead accumulate a
                    # bounded copy and detect+flag at end-of-stream (enforce via non-stream).
                    scan_enabled = resolved.output_guardrail_enabled
                    scanned = bytearray()
                    # Rewrite complete SSE segments before forwarding: drop the induced
                    # usage-only event when the caller did not ask for usage, and strip
                    # reasoning/thinking deltas (matching the non-streaming redaction).
                    drop_usage_only = not client_wants_usage
                    # SSE events can split across network chunks; carry the trailing
                    # partial line so the terminal usage object is parsed - and so no
                    # rewrite ever sees half a ``data:`` line - even when it straddles a
                    # chunk boundary. Bounded so a pathological never-terminated line
                    # cannot grow memory.
                    pending = b""
                    try:
                        chunk = first_chunk
                        while chunk is not None:
                            buffered = pending + chunk
                            # rpartition: everything up to and including the last newline
                            # is complete; with no newline the whole buffer stays pending.
                            complete_lines, newline, pending = buffered.rpartition(b"\n")
                            pending = pending[-65536:]
                            # segment is every byte up to and including the last newline;
                            # guarding on `segment` (not `complete_lines`) keeps a lone
                            # trailing "\n" - e.g. an event terminator flushed in its own
                            # chunk - from being silently dropped.
                            segment = complete_lines + newline
                            if segment:
                                parsed_usage = _usage_from_sse_chunk(segment)
                                if parsed_usage is not None:
                                    usage = parsed_usage
                                segment = _rewrite_stream_segment(
                                    segment, drop_usage_only=drop_usage_only, strip_reasoning=True
                                )
                                if scan_enabled and len(scanned) < 262144:
                                    scanned.extend(segment)
                                if segment:
                                    yield segment
                            try:
                                chunk = await stream.__anext__()
                            except StopAsyncIteration:
                                break
                        if pending:
                            parsed_usage = _usage_from_sse_chunk(pending)
                            if parsed_usage is not None:
                                usage = parsed_usage
                            tail = _rewrite_stream_segment(
                                pending, drop_usage_only=drop_usage_only, strip_reasoning=True
                            )
                            if scan_enabled and len(scanned) < 262144:
                                scanned.extend(tail)
                            if tail:
                                yield tail
                    except httpx.HTTPError as exc:
                        # Upstream failed after headers were sent: emit a terminal SSE
                        # error event and map the recorded status to 502.
                        stream_status = "502"
                        stream_status_code = 502
                        stream_error = "runtime stream failed"
                        AUDIT_LOGGER.debug("runtime stream error: %s", type(exc).__name__)
                        yield _terminal_stream_error_event(stream_backend, request)
                    finally:
                        # True end of stream: record metrics, token usage, and audit now.
                        latency_seconds = perf_counter() - start
                        REQUESTS.labels(route, stream_backend, stream_status).inc()
                        SANDBOX_REQUESTS.labels(
                            _sandbox_label(request.state.sandbox_id), stream_backend, stream_status
                        ).inc()
                        LATENCY.labels(route, stream_backend).observe(latency_seconds)
                        usage_response = {"usage": usage} if usage is not None else None
                        _record_token_usage(stream_backend, usage_response)
                        _record_estimated_cost(resolved, request.state.sandbox_id, stream_backend, usage)
                        if scan_enabled and scanned:
                            patterns, terms = resolved.output_findings(scanned.decode("utf-8", "ignore"))
                            if patterns or terms:
                                OUTPUT_GUARDRAIL.labels("flagged_stream", route).inc()
                        _write_audit_log(
                            resolved,
                            request,
                            payload_dict,
                            status_code=stream_status_code,
                            latency_seconds=latency_seconds,
                            backend=stream_backend,
                            runtime_response=usage_response,
                            error=stream_error,
                        )

                # FastAPI streams this Response object directly; the dict[str, Any] return
                # annotation describes the JSON path and drives the OpenAPI response schema.
                return StreamingResponse(stream_body(), media_type="text/event-stream")  # type: ignore[return-value]
            # Non-streaming: try each route in the chain, failing over to the next on a
            # retryable/connection error or open circuit. `backend` and the payload model
            # are updated to the route that actually served, so metrics and audit reflect it.
            last_exc: httpx.HTTPError | None = None
            for index, candidate in enumerate(chain):
                attempt = dict(payload_dict)
                attempt["model"] = candidate.model_id
                backend = candidate.backend
                try:
                    runtime_response = await client.chat_completions(
                        attempt,
                        headers=_runtime_headers(request),
                        backend=candidate.backend,
                    )
                    payload_dict["model"] = candidate.model_id
                    break
                except httpx.HTTPError as exc:
                    last_exc = exc
                    if _is_failover_worthy(exc) and index + 1 < len(chain):
                        RUNTIME_FALLBACKS.labels(candidate.backend, chain[index + 1].backend).inc()
                        continue
                    raise
            if runtime_response is None:
                raise last_exc or RuntimeError("no runtime route available")
            # Inspect the completion before it is cached or returned: redact/block leaked
            # credentials, PII, or denied content (OWASP LLM02/LLM06). Applied pre-cache so
            # a secret is never persisted in the response cache.
            _apply_output_guardrail(runtime_response, resolved, route, request)
            if cache_enabled:
                await asyncio.to_thread(request.app.state.response_cache.set, cache_id, runtime_response)
            if shadow_route is not None:
                _schedule_shadow(client, shadow_route, payload_dict, request)
            # The finally block reads runtime_response for token-usage metrics and the
            # audit log, so it is bound above rather than returned inline.
            return runtime_response
        except AdmissionPolicyError as exc:
            status_code, headers = _admission_status(exc.reason, resolved)
            status = str(status_code)
            error = str(exc)
            ADMISSION_REJECTIONS.labels(
                exc.reason,
                backend,
                _sandbox_label(request.state.sandbox_id),
            ).inc()
            raise HTTPException(
                status_code=status_code,
                detail={
                    "message": error,
                    "reason": exc.reason,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
                headers=headers,
            ) from exc
        except BudgetBackendError as exc:
            status = "503"
            status_code = 503
            error = "sandbox budget backend is unavailable"
            raise HTTPException(
                status_code=503,
                detail={
                    "message": error,
                    "reason": "budget_backend_unavailable",
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
                headers={"Retry-After": "5"},
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = "502"
            status_code = 502
            runtime_status_code = exc.response.status_code
            error = "runtime returned an error"
            raise HTTPException(
                status_code=502,
                detail={
                    "message": error,
                    "runtime_status": exc.response.status_code,
                    "backend": backend,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
            ) from exc
        except httpx.HTTPError as exc:
            status = "502"
            status_code = 502
            error = "runtime request failed"
            raise HTTPException(
                status_code=502,
                detail={
                    "message": error,
                    "backend": backend,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
            ) from exc
        except ValueError as exc:
            status = "502"
            status_code = 502
            error = "runtime returned an invalid response"
            raise HTTPException(
                status_code=502,
                detail={
                    "message": error,
                    "backend": backend,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
            ) from exc
        finally:
            # Streaming responses record at end-of-stream inside stream_body(); the
            # outer finally only records the non-streaming and error-before-headers paths.
            if not stream_owns_recording:
                REQUESTS.labels(route, backend, status).inc()
                SANDBOX_REQUESTS.labels(_sandbox_label(request.state.sandbox_id), backend, status).inc()
                latency_seconds = perf_counter() - start
                LATENCY.labels(route, backend).observe(latency_seconds)
                if not cache_hit:
                    _record_token_usage(backend, runtime_response)
                    _record_estimated_cost(
                        resolved, request.state.sandbox_id, backend, (runtime_response or {}).get("usage")
                    )
                _write_audit_log(
                    resolved,
                    request,
                    payload_dict,
                    status_code=status_code,
                    latency_seconds=latency_seconds,
                    backend=backend,
                    runtime_response=runtime_response,
                    runtime_status_code=runtime_status_code,
                    error=error,
                )

    @app.post(
        "/v1/completions",
        tags=["inference"],
        summary="Create a private legacy text completion",
        description=(
            "OpenAI-compatible legacy text-completion endpoint (prompt-based, pre-chat). "
            "Routed through the same governance path as chat: model allowlist, admission "
            "limits, prompt secret policy, sandbox budget, output guardrail, and audit. "
            "Streaming is not supported on this endpoint in this release; send stream=false "
            "or use POST /v1/chat/completions for streaming."
        ),
        operation_id="createCompletion",
    )
    async def completions(request: Request, payload: CompletionRequest) -> dict[str, Any]:
        route = "/v1/completions"
        backend = resolved.runtime_backend
        start = perf_counter()
        status = "200"
        status_code = 200
        runtime_status_code = None
        runtime_response = None
        error = None
        payload_dict = payload.model_dump(exclude_none=True)
        request.state.budget_reservation = None
        try:
            policy: ModelRoutingPolicy = request.app.state.model_routing_policy
            sandbox_policies: SandboxPolicySet = request.app.state.sandbox_policy_set
            effective = _effective_settings(request, sandbox_policies, resolved)
            try:
                model_route = policy.resolve(payload_dict.get("model"), effective.model_id)
            except ValueError as exc:
                raise AdmissionPolicyError("model_not_allowed", str(exc)) from exc
            backend = model_route.backend
            payload_dict["model"] = model_route.model_id
            # Streaming for legacy completions is not wired through the SSE usage/guardrail
            # machinery yet; reject it explicitly rather than silently forwarding an
            # unmetered stream. TODO(v0.19+): reuse the chat stream_body path for /v1/completions.
            if payload_dict.get("stream"):
                raise AdmissionPolicyError(
                    "streaming_not_supported",
                    "streaming is not supported on /v1/completions; use /v1/chat/completions",
                )
            effective.validate_completion_admission(payload_dict)
            prompt_action = _apply_prompt_secret_mode(effective, payload_dict, route)
            if prompt_action:
                request.state.prompt_guardrail_action = prompt_action
            # Budget the prompt like chat by synthesizing a messages-shaped payload from the
            # prompt text. Carry the completion-cap fields and n through verbatim (do NOT
            # hardcode max_tokens=0, which is the embeddings "no completion" signal) so
            # budget_delta applies the same cap fallback and n multiplication chat gets -
            # otherwise a caller omitting max_tokens, or using max_completion_tokens or n,
            # would be charged for the prompt only while the runtime generates far more.
            prompt_texts = completion_prompt_texts(payload_dict.get("prompt"))
            budget_payload: dict[str, Any] = {"messages": [{"content": text} for text in prompt_texts]}
            for field in ("max_tokens", "max_completion_tokens", "n"):
                value = payload_dict.get(field)
                if value is not None:
                    budget_payload[field] = value
            tracker: SandboxBudgetTracker = request.app.state.budget_tracker
            reservation = await asyncio.to_thread(tracker.reserve, request.state.sandbox_id, budget_payload, effective)
            request.state.budget_reservation = reservation.audit_dict() if reservation is not None else None
            _record_budget_reservation(reservation, effective)
            request.state.budget_headers = _budget_headers(reservation, effective)
            client: RuntimeClient = request.app.state.runtime_client
            runtime_response = await client.completions(
                payload_dict,
                headers=_runtime_headers(request),
                backend=backend,
            )
            _apply_completion_output_guardrail(runtime_response, resolved, route, request)
            # The finally block reads runtime_response for token-usage metrics and the audit
            # log, so it is bound above rather than returned inline.
            return runtime_response
        except AdmissionPolicyError as exc:
            status_code, headers = _admission_status(exc.reason, resolved)
            status = str(status_code)
            error = str(exc)
            ADMISSION_REJECTIONS.labels(exc.reason, backend, _sandbox_label(request.state.sandbox_id)).inc()
            raise HTTPException(
                status_code=status_code,
                detail={
                    "message": error,
                    "reason": exc.reason,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
                headers=headers,
            ) from exc
        except BudgetBackendError as exc:
            status = "503"
            status_code = 503
            error = "sandbox budget backend is unavailable"
            raise HTTPException(
                status_code=503,
                detail={
                    "message": error,
                    "reason": "budget_backend_unavailable",
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
                headers={"Retry-After": "5"},
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = "502"
            status_code = 502
            runtime_status_code = exc.response.status_code
            error = "runtime returned an error"
            raise HTTPException(
                status_code=502,
                detail={
                    "message": error,
                    "runtime_status": exc.response.status_code,
                    "backend": backend,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
            ) from exc
        except httpx.HTTPError as exc:
            status = "502"
            status_code = 502
            error = "runtime request failed"
            raise HTTPException(
                status_code=502,
                detail={
                    "message": error,
                    "backend": backend,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
            ) from exc
        except ValueError as exc:
            status = "502"
            status_code = 502
            error = "runtime returned an invalid response"
            raise HTTPException(
                status_code=502,
                detail={
                    "message": error,
                    "backend": backend,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
            ) from exc
        finally:
            REQUESTS.labels(route, backend, status).inc()
            SANDBOX_REQUESTS.labels(_sandbox_label(request.state.sandbox_id), backend, status).inc()
            latency_seconds = perf_counter() - start
            LATENCY.labels(route, backend).observe(latency_seconds)
            _record_token_usage(backend, runtime_response)
            _record_estimated_cost(resolved, request.state.sandbox_id, backend, (runtime_response or {}).get("usage"))
            _write_audit_log(
                resolved,
                request,
                payload_dict,
                status_code=status_code,
                latency_seconds=latency_seconds,
                backend=backend,
                runtime_response=runtime_response,
                runtime_status_code=runtime_status_code,
                error=error,
            )

    @app.post(
        "/v1/messages",
        tags=["inference"],
        summary="Create a private Anthropic-style message",
        description=(
            "Native Anthropic Messages API endpoint. The request/response are translated "
            "to and from the internal OpenAI chat shape and routed through the SAME "
            "governance path as POST /v1/chat/completions: model allowlist, admission "
            "limits (including the max_tokens cap), prompt secret policy, sandbox budget, "
            "output guardrail, and audit. Anthropic requires max_tokens; a request omitting "
            "it is rejected. Streaming is not supported on this endpoint in this release; "
            "send stream=false or use POST /v1/chat/completions for OpenAI-shaped streaming."
        ),
        operation_id="createMessage",
    )
    async def messages_endpoint(request: Request, payload: MessagesRequest) -> dict[str, Any]:
        route = "/v1/messages"
        backend = resolved.runtime_backend
        start = perf_counter()
        status = "200"
        status_code = 200
        runtime_status_code = None
        runtime_response = None
        error = None
        # Translate the Anthropic request into the internal OpenAI chat payload up front so
        # the whole governance path (admission, prompt-secret modes, budget, audit) operates
        # on the translated messages exactly as it does for chat - not a second, weaker path.
        payload_dict = anthropic_to_chat_payload(payload)
        request_model = payload.model
        request.state.budget_reservation = None
        try:
            policy: ModelRoutingPolicy = request.app.state.model_routing_policy
            sandbox_policies: SandboxPolicySet = request.app.state.sandbox_policy_set
            effective = _effective_settings(request, sandbox_policies, resolved)
            try:
                model_route = policy.resolve(payload_dict.get("model"), effective.model_id)
            except ValueError as exc:
                raise AdmissionPolicyError("model_not_allowed", str(exc)) from exc
            backend = model_route.backend
            payload_dict["model"] = model_route.model_id
            # Streaming translation to the Anthropic SSE event sequence is not wired through
            # the metering/guardrail machinery yet; reject it explicitly (mirroring
            # /v1/completions' streaming_not_supported) rather than silently forwarding an
            # unmetered or non-Anthropic-shaped stream.
            if payload.stream:
                raise AdmissionPolicyError(
                    "streaming_not_supported",
                    "streaming is not supported on /v1/messages; use /v1/chat/completions",
                )
            # Same admission as chat, on the translated messages: enforces the max_tokens
            # cap, message/prompt-size limits, tool checks, and prompt-secret BLOCK mode.
            effective.validate_admission(payload_dict)
            # Redact/flag prompt secrets (non-block modes) on the translated messages before
            # the payload is reserved or sent, so a redacted credential is never forwarded.
            prompt_action = _apply_prompt_secret_mode(effective, payload_dict, route)
            if prompt_action:
                request.state.prompt_guardrail_action = prompt_action
            tracker: SandboxBudgetTracker = request.app.state.budget_tracker
            reservation = await asyncio.to_thread(tracker.reserve, request.state.sandbox_id, payload_dict, effective)
            request.state.budget_reservation = reservation.audit_dict() if reservation is not None else None
            _record_budget_reservation(reservation, effective)
            request.state.budget_headers = _budget_headers(reservation, effective)
            client: RuntimeClient = request.app.state.runtime_client
            runtime_response = await client.chat_completions(
                payload_dict,
                headers=_runtime_headers(request),
                backend=backend,
            )
            # The output guardrail is endpoint-independent: /v1/messages must not be a bypass
            # around the redact/block policy the chat path enforces (OWASP LLM02/LLM06). It
            # runs on the OpenAI-shaped completion before translation back to Anthropic.
            _apply_output_guardrail(runtime_response, resolved, route, request)
            # Translate the governed OpenAI completion back into an Anthropic Message. The
            # finally block reads runtime_response (the OpenAI shape) for token-usage metrics
            # and the audit fingerprint, so it is bound above rather than returned inline.
            return chat_completion_to_anthropic(runtime_response, request_model=request_model)
        except AdmissionPolicyError as exc:
            status_code, headers = _admission_status(exc.reason, resolved)
            status = str(status_code)
            error = str(exc)
            ADMISSION_REJECTIONS.labels(exc.reason, backend, _sandbox_label(request.state.sandbox_id)).inc()
            raise HTTPException(
                status_code=status_code,
                detail={
                    "message": error,
                    "reason": exc.reason,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
                headers=headers,
            ) from exc
        except BudgetBackendError as exc:
            status = "503"
            status_code = 503
            error = "sandbox budget backend is unavailable"
            raise HTTPException(
                status_code=503,
                detail={
                    "message": error,
                    "reason": "budget_backend_unavailable",
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
                headers={"Retry-After": "5"},
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = "502"
            status_code = 502
            runtime_status_code = exc.response.status_code
            error = "runtime returned an error"
            raise HTTPException(
                status_code=502,
                detail={
                    "message": error,
                    "runtime_status": exc.response.status_code,
                    "backend": backend,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
            ) from exc
        except httpx.HTTPError as exc:
            status = "502"
            status_code = 502
            error = "runtime request failed"
            raise HTTPException(
                status_code=502,
                detail={
                    "message": error,
                    "backend": backend,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
            ) from exc
        except ValueError as exc:
            status = "502"
            status_code = 502
            error = "runtime returned an invalid response"
            raise HTTPException(
                status_code=502,
                detail={
                    "message": error,
                    "backend": backend,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
            ) from exc
        finally:
            REQUESTS.labels(route, backend, status).inc()
            SANDBOX_REQUESTS.labels(_sandbox_label(request.state.sandbox_id), backend, status).inc()
            latency_seconds = perf_counter() - start
            LATENCY.labels(route, backend).observe(latency_seconds)
            _record_token_usage(backend, runtime_response)
            _record_estimated_cost(resolved, request.state.sandbox_id, backend, (runtime_response or {}).get("usage"))
            # Audit the translated (OpenAI-shaped) payload_dict so the fingerprint (message
            # count, roles, prompt hash) is computed the same way as chat - /v1/messages
            # traffic is attributable with the identical redacted receipt shape.
            _write_audit_log(
                resolved,
                request,
                payload_dict,
                status_code=status_code,
                latency_seconds=latency_seconds,
                backend=backend,
                runtime_response=runtime_response,
                runtime_status_code=runtime_status_code,
                error=error,
            )

    @app.post(
        "/v1/responses",
        tags=["inference"],
        summary="Create a private OpenAI Responses API response",
        description=(
            "OpenAI Responses API endpoint (stateless subset). The request/response are "
            "translated to and from the internal OpenAI chat shape and routed through the "
            "SAME governance path as POST /v1/chat/completions: model allowlist, admission "
            "limits (including the max_output_tokens cap), prompt secret policy, sandbox "
            "budget, output guardrail, and audit. This subset is STATELESS: server-side "
            "conversation state is not implemented, so store=true or previous_response_id is "
            "rejected with stateful_not_supported rather than silently ignored. Streaming is "
            "not supported on this endpoint in this release; send stream=false or use POST "
            "/v1/chat/completions for OpenAI-shaped streaming."
        ),
        operation_id="createResponse",
    )
    async def responses_endpoint(request: Request, payload: ResponsesRequest) -> dict[str, Any]:
        route = "/v1/responses"
        backend = resolved.runtime_backend
        start = perf_counter()
        status = "200"
        status_code = 200
        runtime_status_code = None
        runtime_response = None
        error = None
        # Translate the Responses request into the internal OpenAI chat payload up front so
        # the whole governance path (admission, prompt-secret modes, budget, audit) operates
        # on the translated messages exactly as it does for chat - not a second, weaker path.
        payload_dict = responses_to_chat_payload(payload)
        request_model = payload.model
        request.state.budget_reservation = None
        try:
            # Stateless subset: reject any request that asks the server to persist or
            # continue conversation state, rather than silently dropping store /
            # previous_response_id and returning a response the caller wrongly believes was
            # remembered. Checked before admission so it is a clear, deterministic 400.
            if payload.store or payload.previous_response_id is not None:
                raise AdmissionPolicyError(
                    "stateful_not_supported",
                    "server-side response state (store / previous_response_id) is not "
                    "supported on /v1/responses; this is the stateless subset",
                )
            policy: ModelRoutingPolicy = request.app.state.model_routing_policy
            sandbox_policies: SandboxPolicySet = request.app.state.sandbox_policy_set
            effective = _effective_settings(request, sandbox_policies, resolved)
            try:
                model_route = policy.resolve(payload_dict.get("model"), effective.model_id)
            except ValueError as exc:
                raise AdmissionPolicyError("model_not_allowed", str(exc)) from exc
            backend = model_route.backend
            payload_dict["model"] = model_route.model_id
            # Streaming translation to the Responses SSE event sequence is not wired through
            # the metering/guardrail machinery yet; reject it explicitly (mirroring
            # /v1/completions & /v1/messages' streaming_not_supported) rather than silently
            # forwarding an unmetered or non-Responses-shaped stream.
            if payload.stream:
                raise AdmissionPolicyError(
                    "streaming_not_supported",
                    "streaming is not supported on /v1/responses; use /v1/chat/completions",
                )
            # Same admission as chat, on the translated messages: enforces the max_tokens
            # cap (from max_output_tokens), message/prompt-size limits, tool checks, and the
            # prompt-secret BLOCK mode. A missing/empty input translates to no messages and is
            # rejected here by the shared missing_messages check.
            effective.validate_admission(payload_dict)
            # Redact/flag prompt secrets (non-block modes) on the translated messages before
            # the payload is reserved or sent, so a redacted credential is never forwarded.
            prompt_action = _apply_prompt_secret_mode(effective, payload_dict, route)
            if prompt_action:
                request.state.prompt_guardrail_action = prompt_action
            tracker: SandboxBudgetTracker = request.app.state.budget_tracker
            reservation = await asyncio.to_thread(tracker.reserve, request.state.sandbox_id, payload_dict, effective)
            request.state.budget_reservation = reservation.audit_dict() if reservation is not None else None
            _record_budget_reservation(reservation, effective)
            request.state.budget_headers = _budget_headers(reservation, effective)
            client: RuntimeClient = request.app.state.runtime_client
            runtime_response = await client.chat_completions(
                payload_dict,
                headers=_runtime_headers(request),
                backend=backend,
            )
            # The output guardrail is endpoint-independent: /v1/responses must not be a bypass
            # around the redact/block policy the chat path enforces (OWASP LLM02/LLM06). It
            # runs on the OpenAI-shaped completion before translation to the Responses shape.
            _apply_output_guardrail(runtime_response, resolved, route, request)
            # Translate the governed OpenAI completion into a Responses object. The finally
            # block reads runtime_response (the OpenAI shape) for token-usage metrics and the
            # audit fingerprint, so it is bound above rather than returned inline.
            return chat_completion_to_responses(runtime_response, request_model=request_model)
        except AdmissionPolicyError as exc:
            status_code, headers = _admission_status(exc.reason, resolved)
            status = str(status_code)
            error = str(exc)
            ADMISSION_REJECTIONS.labels(exc.reason, backend, _sandbox_label(request.state.sandbox_id)).inc()
            raise HTTPException(
                status_code=status_code,
                detail={
                    "message": error,
                    "reason": exc.reason,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
                headers=headers,
            ) from exc
        except BudgetBackendError as exc:
            status = "503"
            status_code = 503
            error = "sandbox budget backend is unavailable"
            raise HTTPException(
                status_code=503,
                detail={
                    "message": error,
                    "reason": "budget_backend_unavailable",
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
                headers={"Retry-After": "5"},
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = "502"
            status_code = 502
            runtime_status_code = exc.response.status_code
            error = "runtime returned an error"
            raise HTTPException(
                status_code=502,
                detail={
                    "message": error,
                    "runtime_status": exc.response.status_code,
                    "backend": backend,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
            ) from exc
        except httpx.HTTPError as exc:
            status = "502"
            status_code = 502
            error = "runtime request failed"
            raise HTTPException(
                status_code=502,
                detail={
                    "message": error,
                    "backend": backend,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
            ) from exc
        except ValueError as exc:
            status = "502"
            status_code = 502
            error = "runtime returned an invalid response"
            raise HTTPException(
                status_code=502,
                detail={
                    "message": error,
                    "backend": backend,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
            ) from exc
        finally:
            REQUESTS.labels(route, backend, status).inc()
            SANDBOX_REQUESTS.labels(_sandbox_label(request.state.sandbox_id), backend, status).inc()
            latency_seconds = perf_counter() - start
            LATENCY.labels(route, backend).observe(latency_seconds)
            _record_token_usage(backend, runtime_response)
            _record_estimated_cost(resolved, request.state.sandbox_id, backend, (runtime_response or {}).get("usage"))
            # Audit the translated (OpenAI-shaped) payload_dict so the fingerprint (message
            # count, roles, prompt hash) is computed the same way as chat - /v1/responses
            # traffic is attributable with the identical redacted receipt shape.
            _write_audit_log(
                resolved,
                request,
                payload_dict,
                status_code=status_code,
                latency_seconds=latency_seconds,
                backend=backend,
                runtime_response=runtime_response,
                runtime_status_code=runtime_status_code,
                error=error,
            )

    @app.post(
        "/v1/embeddings",
        tags=["inference"],
        summary="Create private embeddings",
        operation_id="createEmbeddings",
    )
    async def embeddings(request: Request, payload: EmbeddingsRequest) -> dict[str, Any]:
        route = "/v1/embeddings"
        backend = resolved.runtime_backend
        start = perf_counter()
        status = "200"
        status_code = 200
        runtime_status_code = None
        runtime_response = None
        error = None
        payload_dict = payload.model_dump(exclude_none=True)
        request.state.budget_reservation = None
        try:
            policy: ModelRoutingPolicy = request.app.state.model_routing_policy
            sandbox_policies: SandboxPolicySet = request.app.state.sandbox_policy_set
            effective = _effective_settings(request, sandbox_policies, resolved)
            try:
                model_route = policy.resolve(payload_dict.get("model"), effective.model_id)
            except ValueError as exc:
                raise AdmissionPolicyError("model_not_allowed", str(exc)) from exc
            backend = model_route.backend
            payload_dict["model"] = model_route.model_id
            effective.validate_embedding_admission(payload_dict)
            prompt_action = _apply_prompt_secret_mode(effective, payload_dict, route)
            if prompt_action:
                request.state.prompt_guardrail_action = prompt_action
            # Count embedding inputs against the sandbox budget the same way prompts are.
            raw_input = payload_dict.get("input")
            texts = raw_input if isinstance(raw_input, list) else [raw_input]
            budget_payload = {"messages": [{"content": str(text)} for text in texts], "max_tokens": 0}
            tracker: SandboxBudgetTracker = request.app.state.budget_tracker
            reservation = await asyncio.to_thread(tracker.reserve, request.state.sandbox_id, budget_payload, effective)
            request.state.budget_reservation = reservation.audit_dict() if reservation is not None else None
            _record_budget_reservation(reservation, effective)
            request.state.budget_headers = _budget_headers(reservation, effective)
            client: RuntimeClient = request.app.state.runtime_client
            runtime_response = await client.embeddings(
                payload_dict,
                headers=_runtime_headers(request),
                backend=backend,
            )
            # Bound before returning: the finally block reads runtime_response for
            # token usage and the audit log, so this assignment is not redundant.
            return runtime_response  # noqa: RET504
        except AdmissionPolicyError as exc:
            status_code, headers = _admission_status(exc.reason, resolved)
            status = str(status_code)
            error = str(exc)
            ADMISSION_REJECTIONS.labels(exc.reason, backend, _sandbox_label(request.state.sandbox_id)).inc()
            raise HTTPException(
                status_code=status_code,
                detail={
                    "message": error,
                    "reason": exc.reason,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
                headers=headers,
            ) from exc
        except BudgetBackendError as exc:
            status = "503"
            status_code = 503
            error = "sandbox budget backend is unavailable"
            raise HTTPException(
                status_code=503,
                detail={
                    "message": error,
                    "reason": "budget_backend_unavailable",
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
                headers={"Retry-After": "5"},
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = "502"
            status_code = 502
            runtime_status_code = exc.response.status_code
            error = "runtime returned an error"
            raise HTTPException(
                status_code=502,
                detail={
                    "message": error,
                    "runtime_status": exc.response.status_code,
                    "backend": backend,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
            ) from exc
        except httpx.HTTPError as exc:
            status = "502"
            status_code = 502
            error = "runtime request failed"
            raise HTTPException(
                status_code=502,
                detail={
                    "message": error,
                    "backend": backend,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
            ) from exc
        except ValueError as exc:
            status = "502"
            status_code = 502
            error = "runtime returned an invalid response"
            raise HTTPException(
                status_code=502,
                detail={
                    "message": error,
                    "backend": backend,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
            ) from exc
        finally:
            REQUESTS.labels(route, backend, status).inc()
            SANDBOX_REQUESTS.labels(_sandbox_label(request.state.sandbox_id), backend, status).inc()
            latency_seconds = perf_counter() - start
            LATENCY.labels(route, backend).observe(latency_seconds)
            _record_token_usage(backend, runtime_response)
            _record_estimated_cost(resolved, request.state.sandbox_id, backend, (runtime_response or {}).get("usage"))
            _write_audit_log(
                resolved,
                request,
                payload_dict,
                status_code=status_code,
                latency_seconds=latency_seconds,
                backend=backend,
                runtime_response=runtime_response,
                runtime_status_code=runtime_status_code,
                error=error,
            )

    @app.post(
        "/v1/moderations",
        tags=["inference"],
        summary="Classify content against the gateway policy",
        operation_id="createModeration",
    )
    async def moderations(request: Request, payload: ModerationRequest) -> dict[str, Any]:
        route = "/v1/moderations"
        start = perf_counter()
        status = "200"
        status_code = 200
        error = None
        payload_dict = payload.model_dump(exclude_none=True)
        try:
            raw_input = payload_dict.get("input")
            texts = raw_input if isinstance(raw_input, list) else [raw_input]
            texts = [str(item) for item in texts if item is not None]
            if not texts:
                raise AdmissionPolicyError("missing_input", "moderations request must include non-empty input")
            # Same admission ceiling as chat/embeddings: without it this is the one
            # endpoint where an arbitrarily large body reaches every classifier regex.
            total_chars = sum(len(text) for text in texts)
            if total_chars > resolved.max_prompt_chars:
                raise AdmissionPolicyError(
                    "input_too_large",
                    f"moderations input has {total_chars} characters; limit is {resolved.max_prompt_chars}",
                )
            results = [moderate_text(text, resolved) for text in texts]
            return {
                "id": f"modr-{request.state.request_id}",
                "model": payload_dict.get("model") or "platform-content-policy",
                # Honesty marker: these categories are the governance taxonomy
                # (credential/pii/blocked_terms), NOT OpenAI's harm taxonomy. The field
                # lets a client tell the two response shapes apart.
                "taxonomy": "governance",
                "results": results,
            }
        except AdmissionPolicyError as exc:
            status_code = 400
            status = "400"
            error = str(exc)
            raise HTTPException(
                status_code=400,
                detail={
                    "message": error,
                    "reason": exc.reason,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
            ) from exc
        finally:
            REQUESTS.labels(route, resolved.runtime_backend, status).inc()
            SANDBOX_REQUESTS.labels(_sandbox_label(request.state.sandbox_id), resolved.runtime_backend, status).inc()
            latency_seconds = perf_counter() - start
            LATENCY.labels(route, resolved.runtime_backend).observe(latency_seconds)
            _write_audit_log(
                resolved,
                request,
                payload_dict,
                status_code=status_code,
                latency_seconds=latency_seconds,
                backend=resolved.runtime_backend,
                error=error,
            )

    async def _run_batch(request: Request, payload: BatchRequest, route: str) -> dict[str, Any]:
        start = perf_counter()
        status = "200"
        status_code = 200
        error = None
        # Per-item audit fingerprints (redacted counts + prompt hash, same fields as
        # single requests) so batch traffic is attributable item-by-item, not just as
        # an opaque batch_size. Defined before the try so the audit finally always
        # sees it, including on whole-batch rejections.
        audit_items: list[dict[str, Any]] = []
        try:
            if len(payload.requests) > resolved.max_batch_requests:
                raise AdmissionPolicyError(
                    "batch_too_large",
                    f"batch has {len(payload.requests)} requests; limit is {resolved.max_batch_requests}",
                )
            policy: ModelRoutingPolicy = request.app.state.model_routing_policy
            sandbox_policies: SandboxPolicySet = request.app.state.sandbox_policy_set
            effective = _effective_settings(request, sandbox_policies, resolved)
            tracker: SandboxBudgetTracker = request.app.state.budget_tracker
            client: RuntimeClient = request.app.state.runtime_client
            # Bound per-batch fan-out so one batch cannot saturate the upstream pool.
            semaphore = asyncio.Semaphore(min(8, max(1, len(payload.requests))))

            def _audit_item(
                index: int, status_code: int, item_dict: dict[str, Any], prompt_guardrail_action: str | None = None
            ) -> None:
                entry: dict[str, Any] = {
                    "index": index,
                    "status_code": status_code,
                    "model": item_dict.get("model") or effective.model_id,
                }
                if prompt_guardrail_action is not None:
                    entry["prompt_guardrail_action"] = prompt_guardrail_action
                entry.update(_payload_fingerprint(item_dict))
                audit_items.append(entry)

            async def _process(index: int, item: ChatCompletionRequest) -> dict[str, Any]:
                item_dict = item.model_dump(exclude_none=True)
                item_dict.pop("stream", None)
                item_prompt_action: str | None = None
                async with semaphore:
                    try:
                        try:
                            model_route = policy.resolve(item_dict.get("model"), effective.model_id)
                        except ValueError as exc:
                            raise AdmissionPolicyError("model_not_allowed", str(exc)) from exc
                        item_dict["model"] = model_route.model_id
                        effective.validate_admission(item_dict)
                        # Attribute the redact/flag action to this item's audit receipt rather
                        # than the one shared per-request field (concurrent items would race it).
                        item_prompt_action = _apply_prompt_secret_mode(effective, item_dict, route)
                        await asyncio.to_thread(tracker.reserve, request.state.sandbox_id, item_dict, effective)
                        response = await client.chat_completions(
                            item_dict, headers=_runtime_headers(request), backend=model_route.backend
                        )
                        # The output guardrail is an endpoint-independent control: a batch
                        # item must not be a bypass around the redact/block policy that the
                        # single-request path enforces (OWASP LLM02/LLM06).
                        _apply_output_guardrail(response, resolved, route, request)
                        _record_token_usage(model_route.backend, response)
                        _record_estimated_cost(
                            resolved,
                            request.state.sandbox_id,
                            model_route.backend,
                            response.get("usage") if isinstance(response, dict) else None,
                        )
                        _audit_item(index, 200, item_dict, item_prompt_action)
                        return {"index": index, "status_code": 200, "response": response}
                    except AdmissionPolicyError as exc:
                        item_code, _ = _admission_status(exc.reason, resolved)
                        _audit_item(index, item_code, item_dict)
                        return {
                            "index": index,
                            "status_code": item_code,
                            "error": {"reason": exc.reason, "message": str(exc)},
                        }
                    except httpx.HTTPStatusError as exc:
                        _audit_item(index, 502, item_dict, item_prompt_action)
                        return {
                            "index": index,
                            "status_code": 502,
                            "error": {
                                "message": "runtime returned an error",
                                "runtime_status": exc.response.status_code,
                            },
                        }
                    except BudgetBackendError:
                        _audit_item(index, 503, item_dict, item_prompt_action)
                        return {
                            "index": index,
                            "status_code": 503,
                            "error": {"reason": "budget_backend_unavailable", "message": "budget backend unavailable"},
                        }
                    except (httpx.HTTPError, ValueError):
                        _audit_item(index, 502, item_dict, item_prompt_action)
                        return {"index": index, "status_code": 502, "error": {"message": "runtime request failed"}}

            results = await asyncio.gather(*[_process(index, item) for index, item in enumerate(payload.requests)])
            return {"object": "batch", "count": len(results), "results": list(results)}
        except AdmissionPolicyError as exc:
            status_code, headers = _admission_status(exc.reason, resolved)
            status = str(status_code)
            error = str(exc)
            raise HTTPException(
                status_code=status_code,
                detail={
                    "message": error,
                    "reason": exc.reason,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
                headers=headers,
            ) from exc
        finally:
            REQUESTS.labels(route, resolved.runtime_backend, status).inc()
            SANDBOX_REQUESTS.labels(_sandbox_label(request.state.sandbox_id), resolved.runtime_backend, status).inc()
            latency_seconds = perf_counter() - start
            LATENCY.labels(route, resolved.runtime_backend).observe(latency_seconds)
            if resolved.audit_log_enabled:
                event = {
                    "event": "batch_request",
                    # Per-process chain identity (hash-covered); see _write_audit_log.
                    "chain_id": getattr(request.app.state, "audit_chain_id", None),
                    "action_type": "model_call",
                    "decision": "allowed" if status_code < 400 else "denied",
                    "request_id": request.state.request_id,
                    "traceparent": request.state.traceparent,
                    "sandbox_id": request.state.sandbox_id,
                    "principal": getattr(request.state, "principal", None),
                    "batch_size": len(payload.requests),
                    "items": sorted(audit_items, key=lambda entry: entry["index"]),
                    "status_code": status_code,
                    "latency_ms": round(latency_seconds * 1000, 2),
                    "ts": time(),
                    "error": error,
                }
                _chain_audit_event(request, event)
                # Emit to both the audit logger and uvicorn.error so batch receipts reach
                # pod logs (kubectl logs / Loki) exactly like inference_request events; a
                # receipt that advanced the chain but was invisible would read as a hole
                # to an operator verifying the chain against the logs.
                batch_line = json.dumps(event, sort_keys=True)
                AUDIT_LOGGER.info(batch_line)
                logging.getLogger("uvicorn.error").info(batch_line)

    _BATCH_DESCRIPTION = (
        "Synchronous, size-bounded (MAX_BATCH_REQUESTS) fan-out that runs every item "
        "concurrently and returns per-item results inline. This is NOT the OpenAI "
        "asynchronous file-batch API: there is no batch id, status polling, result-file "
        "retrieval, or cancellation. Use it for small offline batches that fit within the "
        "request timeout and gateway concurrency limit."
    )

    @app.post(
        "/v1/batch-inference",
        tags=["inference"],
        summary="Process a batch of chat completions synchronously",
        description=_BATCH_DESCRIPTION,
        operation_id="createBatchInference",
    )
    async def batch_inference(request: Request, payload: BatchRequest) -> dict[str, Any]:
        return await _run_batch(request, payload, "/v1/batch-inference")

    @app.post(
        "/v1/batches",
        tags=["inference"],
        summary="Process a batch of chat completions synchronously (deprecated path)",
        description=(
            _BATCH_DESCRIPTION + " Deprecated: this path is renamed to /v1/batch-inference to avoid colliding "
            "with the name of OpenAI's asynchronous file-batch API; it still works but sends "
            "a Deprecation response header and will be removed in a future release. Migrate to "
            'POST /v1/batch-inference (Link: </v1/batch-inference>; rel="successor-version").'
        ),
        operation_id="createBatch",
    )
    async def batches(request: Request, payload: BatchRequest, response: Response) -> dict[str, Any]:
        # Signal the rename to clients per RFC 8594 while keeping the old path working for
        # one release; Link points at the successor so tooling can follow it automatically.
        response.headers["Deprecation"] = "true"
        response.headers["Link"] = '</v1/batch-inference>; rel="successor-version"'
        return await _run_batch(request, payload, "/v1/batches")

    _install_openapi_contract(app, resolved)
    return app


app = create_app()
