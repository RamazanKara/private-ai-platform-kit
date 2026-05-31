import hashlib
import hmac
import json
import logging
import re
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel

from app.budget import BudgetReservation, SandboxBudgetTracker, build_sandbox_budget_tracker
from app.runtime_client import RuntimeClient
from app.settings import AdmissionPolicyError, Settings, validate_sandbox_id


AUDIT_LOGGER = logging.getLogger("ai_platform_ops_lab.audit")
TRACEPARENT_PATTERN = re.compile(r"^[\da-f]{2}-[\da-f]{32}-[\da-f]{16}-[\da-f]{2}$")

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


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[Message]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool | None = False


def _request_id_from_header(request: Request) -> str:
    request_id = request.headers.get("x-request-id", "").strip()
    if not request_id:
        return str(uuid4())
    if len(request_id) > 128 or any(char.isspace() for char in request_id):
        raise ValueError("X-Request-ID must be 1-128 visible characters without spaces")
    return request_id


def _traceparent_from_header(request: Request) -> str | None:
    traceparent = request.headers.get("traceparent")
    if traceparent is None:
        return None
    traceparent = traceparent.strip().lower()
    if not TRACEPARENT_PATTERN.fullmatch(traceparent):
        raise ValueError("traceparent must use W3C trace context format")
    return traceparent


def _runtime_headers(request: Request) -> dict[str, str]:
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
    return path not in {"/healthz", "/metrics", "/docs", "/openapi.json"}


def _api_key_from_request(request: Request, settings: Settings) -> str | None:
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
    api_key = _api_key_from_request(request, settings)
    if not api_key:
        return False
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    return any(hmac.compare_digest(digest, expected) for expected in settings.api_key_sha256s)


def _auth_failure_response(request: Request, reason: str) -> JSONResponse:
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


def _payload_fingerprint(payload: dict[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages") or []
    canonical_messages = []
    roles = []
    prompt_chars = 0
    for message in messages:
        role = str(message.get("role", "unknown"))
        content = str(message.get("content", ""))
        roles.append(role)
        prompt_chars += len(content)
        canonical_messages.append({"role": role, "content": content})
    canonical = json.dumps(canonical_messages, sort_keys=True, separators=(",", ":"))
    return {
        "message_count": len(messages),
        "message_roles": roles,
        "prompt_chars": prompt_chars,
        "prompt_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _record_token_usage(backend: str, runtime_response: dict[str, Any] | None) -> None:
    usage = (runtime_response or {}).get("usage")
    if not isinstance(usage, dict):
        return
    for token_type in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(token_type)
        if isinstance(value, (int, float)) and value >= 0:
            TOKEN_USAGE.labels(backend, token_type).inc(value)


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
        SANDBOX_BUDGET_USAGE.labels(reservation.sandbox_id, budget_type).set(value)
        SANDBOX_BUDGET_LIMIT.labels(reservation.sandbox_id, budget_type).set(
            limits[budget_type]
        )


def _write_audit_log(
    settings: Settings,
    request: Request,
    payload: dict[str, Any],
    status_code: int,
    latency_seconds: float,
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
        "backend": settings.runtime_backend,
        "model": payload.get("model") or settings.model_id,
        "status_code": status_code,
        "runtime_status_code": runtime_status_code,
        "latency_ms": round(latency_seconds * 1000, 2),
        "usage": (runtime_response or {}).get("usage"),
        "error": error,
        "budget": getattr(request.state, "budget_reservation", None),
    }
    event.update(_payload_fingerprint(payload))
    line = json.dumps(event, sort_keys=True)
    AUDIT_LOGGER.info(line)
    logging.getLogger("uvicorn.error").info(line)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    app = FastAPI(
        title="Private AI Platform Kit Inference Gateway",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.settings = resolved
    app.state.runtime_client = RuntimeClient(resolved)
    app.state.budget_tracker = build_sandbox_budget_tracker(resolved)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        try:
            request.state.request_id = _request_id_from_header(request)
            request.state.sandbox_id = validate_sandbox_id(
                request.headers.get("x-sandbox-id", resolved.default_sandbox_id)
            )
            request.state.traceparent = _traceparent_from_header(request)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"detail": str(exc)})

        if resolved.api_key_auth_enabled and _auth_required(request.url.path):
            if not _valid_api_key(request, resolved):
                return _auth_failure_response(request, "invalid_or_missing_api_key")

        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Sandbox-ID"] = request.state.sandbox_id
        if request.state.traceparent:
            response.headers["traceparent"] = request.state.traceparent
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        REQUESTS.labels("/healthz", resolved.runtime_backend, "200").inc()
        return {
            "status": "ok",
            "backend": resolved.runtime_backend,
            "model": resolved.model_id,
        }

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/sandbox/budget")
    async def sandbox_budget(request: Request) -> dict[str, Any]:
        tracker: SandboxBudgetTracker = request.app.state.budget_tracker
        return tracker.snapshot(request.state.sandbox_id)

    @app.post("/v1/chat/completions")
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
        try:
            resolved.validate_admission(payload_dict)
            tracker: SandboxBudgetTracker = request.app.state.budget_tracker
            reservation = tracker.reserve(request.state.sandbox_id, payload_dict)
            request.state.budget_reservation = (
                reservation.audit_dict() if reservation is not None else None
            )
            _record_budget_reservation(reservation, resolved)
            client: RuntimeClient = request.app.state.runtime_client
            runtime_response = await client.chat_completions(
                payload_dict,
                headers=_runtime_headers(request),
            )
            return runtime_response
        except AdmissionPolicyError as exc:
            status = "400"
            status_code = 400
            error = str(exc)
            ADMISSION_REJECTIONS.labels(
                exc.reason,
                backend,
                request.state.sandbox_id,
            ).inc()
            raise HTTPException(
                status_code=400,
                detail={
                    "message": error,
                    "reason": exc.reason,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
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
        except (httpx.HTTPError, ValueError) as exc:
            status = "502"
            status_code = 502
            error = str(exc)
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
            SANDBOX_REQUESTS.labels(request.state.sandbox_id, backend, status).inc()
            latency_seconds = perf_counter() - start
            LATENCY.labels(route, backend).observe(latency_seconds)
            _record_token_usage(backend, runtime_response)
            _write_audit_log(
                resolved,
                request,
                payload_dict,
                status_code=status_code,
                latency_seconds=latency_seconds,
                runtime_response=runtime_response,
                runtime_status_code=runtime_status_code,
                error=error,
            )

    return app


app = create_app()
