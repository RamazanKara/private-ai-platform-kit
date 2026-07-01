"""OpenAI-compatible inference gateway with auth, admission, budgets, and runtime routing."""

import asyncio
import hashlib
import hmac
import json
import logging
import random
import re
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, ConfigDict

from app.budget import (
    BudgetBackendError,
    BudgetReservation,
    SandboxBudgetTracker,
    build_sandbox_budget_tracker,
)
from app.cache import build_response_cache, cache_key
from app.jwt_auth import JwksUnavailableError, JwtAuthError, JwtVerifier
from app.policy import ModelRoutingPolicy, SandboxPolicySet
from app.ratelimit import build_rate_limiter
from app.runtime_client import RuntimeClient
from app.settings import (
    AdmissionPolicyError,
    Settings,
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
SERVICE_VERSION = "0.13.0"
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


def _valid_api_key(request: Request, settings: Settings) -> bool:
    """Return whether the request carries an API key matching a configured digest."""
    api_key = _api_key_from_request(request, settings)
    if not api_key:
        return False
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    return any(hmac.compare_digest(digest, expected) for expected in settings.api_key_sha256s)


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


def _api_key_principal(request: Request, settings: Settings) -> dict[str, Any]:
    """Build a non-reversible audit principal for an API-key caller.

    The key itself is never logged; ``key_id`` is a stable digest prefix so audit
    consumers can attribute requests to a specific issued key.
    """
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
        content={
            "detail": {
                "message": "authentication required",
                "reason": reason,
                "request_id": request.state.request_id,
                "sandbox_id": request.state.sandbox_id,
            }
        },
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
        content={
            "detail": {
                "message": "authentication is temporarily unavailable",
                "reason": "jwks_unavailable",
                "request_id": request.state.request_id,
                "sandbox_id": request.state.sandbox_id,
            }
        },
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
        content={
            "detail": {
                "message": "gateway is at capacity; retry shortly",
                "reason": "concurrency_limit",
                "request_id": request.state.request_id,
                "sandbox_id": request.state.sandbox_id,
            }
        },
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
        content={
            "detail": {
                "message": "rate limit backend is unavailable; retry shortly",
                "reason": "rate_limit_backend_unavailable",
                "request_id": request.state.request_id,
                "sandbox_id": request.state.sandbox_id,
            }
        },
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
        content={
            "detail": {
                "message": "rate limit exceeded for this sandbox",
                "reason": "rate_limited",
                "request_id": request.state.request_id,
                "sandbox_id": request.state.sandbox_id,
            }
        },
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
        content={
            "detail": {
                "message": "sandbox identity is not authorized for this caller",
                "reason": reason,
                "request_id": request.state.request_id,
                "sandbox_id": request.state.sandbox_id,
            }
        },
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
    if not messages and payload.get("input") is not None:
        raw = payload["input"]
        texts = [str(item) for item in (raw if isinstance(raw, list) else [raw])]
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


def _usage_from_sse_chunk(chunk: bytes) -> dict[str, Any] | None:
    """Return the ``usage`` object from a terminal SSE chunk, or None when absent.

    OpenAI-compatible streams emit a final ``data:`` event carrying a ``usage``
    object (when usage reporting is enabled) before ``data: [DONE]``. Each chunk may
    contain several SSE events; scan them and return the last usage object found.
    """
    found: dict[str, Any] | None = None
    for line in chunk.split(b"\n"):
        line = line.strip()
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if not data or data == b"[DONE]":
            continue
        try:
            parsed = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("usage"), dict):
            found = parsed["usage"]
    return found


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
    event = {
        "event": "inference_request",
        "request_id": request.state.request_id,
        "traceparent": request.state.traceparent,
        "sandbox_id": request.state.sandbox_id,
        "principal": getattr(request.state, "principal", None),
        "backend": backend,
        "model": payload.get("model") or settings.model_id,
        "status_code": status_code,
        "runtime_status_code": runtime_status_code,
        "latency_ms": round(latency_seconds * 1000, 2),
        "usage": (runtime_response or {}).get("usage"),
        "error": error,
        "budget": getattr(request.state, "budget_reservation", None),
    }
    event.update(_payload_fingerprint(payload))
    _chain_audit_event(request, event)
    line = json.dumps(event, sort_keys=True)
    AUDIT_LOGGER.info(line)
    logging.getLogger("uvicorn.error").info(line)


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
    app.state.background_tasks = set()
    app.state.response_cache = build_response_cache(resolved)
    app.state.model_routing_policy = (
        ModelRoutingPolicy.from_path(resolved.model_routing_policy_path, resolved)
        if resolved.model_routing_policy_path
        else ModelRoutingPolicy.default(resolved)
    )
    app.state.sandbox_policy_set = SandboxPolicySet.from_path(resolved.sandbox_policy_path)
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
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"detail": str(exc)})

        async def dispatch() -> Response:
            if (resolved.api_key_auth_enabled or resolved.jwt_auth_enabled) and _auth_required(request.url.path):
                api_key_ok = resolved.api_key_auth_enabled and _valid_api_key(request, resolved)
                jwt_claims: dict[str, Any] | None = None
                if not api_key_ok and resolved.jwt_auth_enabled:
                    try:
                        jwt_claims = await _valid_jwt(request, request.app.state.jwt_verifier)
                    except JwksUnavailableError:
                        # Issuer JWKS is unreachable: this is a 503 (retry later),
                        # not a 401 token rejection.
                        return _jwks_unavailable_response(request)
                if not api_key_ok and jwt_claims is None:
                    return _auth_failure_response(request, "invalid_or_missing_api_key")
                # Propagate the authenticated principal so the audit trail records who
                # called, not just the (client-asserted) sandbox header.
                if api_key_ok:
                    request.state.principal = _api_key_principal(request, resolved)
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
                    return _rate_limit_backend_unavailable_response(request)
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
            if getattr(request.state, "output_guardrail_action", None):
                response.headers["X-Output-Guardrail"] = request.state.output_guardrail_action
            return response

        tracer = request.app.state.tracer
        if tracer is None:
            return await dispatch()
        return await trace_request(tracer, request, dispatch)

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
        tracker: SandboxBudgetTracker = request.app.state.budget_tracker
        policy_set: SandboxPolicySet = request.app.state.sandbox_policy_set
        effective = policy_set.effective_settings(resolved, request.state.sandbox_id)
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
        tracker: SandboxBudgetTracker = request.app.state.budget_tracker
        policy_set: SandboxPolicySet = request.app.state.sandbox_policy_set
        effective = policy_set.effective_settings(resolved, request.state.sandbox_id)
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
            effective = sandbox_policies.effective_settings(resolved, request.state.sandbox_id)
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
            client: RuntimeClient = request.app.state.runtime_client
            if payload_dict.get("stream"):
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
                    # SSE events can split across network chunks; carry the trailing
                    # partial line so the terminal usage object is parsed even when the
                    # ``data:`` line straddles a chunk boundary. Bounded so a pathological
                    # never-terminated line cannot grow memory.
                    pending = b""
                    try:
                        chunk = first_chunk
                        while chunk is not None:
                            buffered = pending + chunk
                            # rpartition: everything before the last newline is complete;
                            # with no newline the whole buffer stays pending.
                            complete_lines, _, pending = buffered.rpartition(b"\n")
                            pending = pending[-65536:]
                            parsed_usage = _usage_from_sse_chunk(complete_lines)
                            if parsed_usage is not None:
                                usage = parsed_usage
                            if scan_enabled and len(scanned) < 262144:
                                scanned.extend(chunk)
                            yield chunk
                            try:
                                chunk = await stream.__anext__()
                            except StopAsyncIteration:
                                break
                        if pending:
                            parsed_usage = _usage_from_sse_chunk(pending)
                            if parsed_usage is not None:
                                usage = parsed_usage
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
            effective = sandbox_policies.effective_settings(resolved, request.state.sandbox_id)
            try:
                model_route = policy.resolve(payload_dict.get("model"), effective.model_id)
            except ValueError as exc:
                raise AdmissionPolicyError("model_not_allowed", str(exc)) from exc
            backend = model_route.backend
            payload_dict["model"] = model_route.model_id
            effective.validate_embedding_admission(payload_dict)
            # Count embedding inputs against the sandbox budget the same way prompts are.
            raw_input = payload_dict.get("input")
            texts = raw_input if isinstance(raw_input, list) else [raw_input]
            budget_payload = {"messages": [{"content": str(text)} for text in texts], "max_tokens": 0}
            tracker: SandboxBudgetTracker = request.app.state.budget_tracker
            reservation = await asyncio.to_thread(tracker.reserve, request.state.sandbox_id, budget_payload, effective)
            request.state.budget_reservation = reservation.audit_dict() if reservation is not None else None
            _record_budget_reservation(reservation, effective)
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

    @app.post(
        "/v1/batches",
        tags=["inference"],
        summary="Process a batch of chat completions synchronously",
        description=(
            "Synchronous, size-bounded (MAX_BATCH_REQUESTS) fan-out that runs every item "
            "concurrently and returns per-item results inline. This is NOT the OpenAI "
            "asynchronous file-batch API: there is no batch id, status polling, result-file "
            "retrieval, or cancellation. Use it for small offline batches that fit within the "
            "request timeout and gateway concurrency limit."
        ),
        operation_id="createBatch",
    )
    async def batches(request: Request, payload: BatchRequest) -> dict[str, Any]:
        route = "/v1/batches"
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
            effective = sandbox_policies.effective_settings(resolved, request.state.sandbox_id)
            tracker: SandboxBudgetTracker = request.app.state.budget_tracker
            client: RuntimeClient = request.app.state.runtime_client
            # Bound per-batch fan-out so one batch cannot saturate the upstream pool.
            semaphore = asyncio.Semaphore(min(8, max(1, len(payload.requests))))

            def _audit_item(index: int, status_code: int, item_dict: dict[str, Any]) -> None:
                entry: dict[str, Any] = {
                    "index": index,
                    "status_code": status_code,
                    "model": item_dict.get("model") or effective.model_id,
                }
                entry.update(_payload_fingerprint(item_dict))
                audit_items.append(entry)

            async def _process(index: int, item: ChatCompletionRequest) -> dict[str, Any]:
                item_dict = item.model_dump(exclude_none=True)
                item_dict.pop("stream", None)
                async with semaphore:
                    try:
                        try:
                            model_route = policy.resolve(item_dict.get("model"), effective.model_id)
                        except ValueError as exc:
                            raise AdmissionPolicyError("model_not_allowed", str(exc)) from exc
                        item_dict["model"] = model_route.model_id
                        effective.validate_admission(item_dict)
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
                        _audit_item(index, 200, item_dict)
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
                        _audit_item(index, 502, item_dict)
                        return {
                            "index": index,
                            "status_code": 502,
                            "error": {
                                "message": "runtime returned an error",
                                "runtime_status": exc.response.status_code,
                            },
                        }
                    except BudgetBackendError:
                        _audit_item(index, 503, item_dict)
                        return {
                            "index": index,
                            "status_code": 503,
                            "error": {"reason": "budget_backend_unavailable", "message": "budget backend unavailable"},
                        }
                    except (httpx.HTTPError, ValueError):
                        _audit_item(index, 502, item_dict)
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
                    "request_id": request.state.request_id,
                    "traceparent": request.state.traceparent,
                    "sandbox_id": request.state.sandbox_id,
                    "principal": getattr(request.state, "principal", None),
                    "batch_size": len(payload.requests),
                    "items": sorted(audit_items, key=lambda entry: entry["index"]),
                    "status_code": status_code,
                    "latency_ms": round(latency_seconds * 1000, 2),
                    "error": error,
                }
                _chain_audit_event(request, event)
                AUDIT_LOGGER.info(json.dumps(event, sort_keys=True))

    _install_openapi_contract(app, resolved)
    return app


app = create_app()
