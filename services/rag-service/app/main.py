"""FastAPI RAG service exposing traceable, auth-gated knowledge retrieval endpoints."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

from app.embeddings import build_embedding_provider
from app.retriever import LexicalRetriever, QdrantRetriever, VectorStoreError, build_context
from app.settings import Settings, validate_sandbox_id
from app.tracing import configure_tracing, trace_request

AUDIT_LOGGER = logging.getLogger("ai_platform_ops_lab.rag.audit")
TRACEPARENT_PATTERN = re.compile(r"^[\da-f]{2}-[\da-f]{32}-[\da-f]{16}-[\da-f]{2}$")
SERVICE_VERSION = "0.9.0"
OPENAPI_DESCRIPTION = (
    "Private retrieval service for platform and customer knowledge. The service "
    "returns traceable retrieval results, optional context blocks, and "
    "OpenAI-compatible grounded messages for downstream chat-completion calls."
)
OPENAPI_TAGS = [
    {"name": "health", "description": "Readiness and liveness checks."},
    {"name": "observability", "description": "Prometheus metrics endpoints."},
    {"name": "retrieval", "description": "Knowledge document and RAG query endpoints."},
]

REQUESTS = Counter(
    "rag_service_requests_total",
    "Total RAG service requests by route and status.",
    ["route", "status"],
)
LATENCY = Histogram(
    "rag_service_request_duration_seconds",
    "RAG service request latency by route.",
    ["route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
RETRIEVAL_RESULTS = Histogram(
    "rag_service_retrieval_results",
    "Number of retrieved documents by sandbox.",
    ["sandbox"],
    buckets=(0, 1, 2, 3, 5, 8, 13),
)
AUTH_FAILURES = Counter(
    "rag_service_auth_failures_total",
    "Total RAG service authentication failures by route and reason.",
    ["route", "reason"],
)


class RagQueryRequest(BaseModel):
    """Request body for a RAG retrieval query with context and message options."""

    query: str = Field(..., min_length=1)
    top_k: int | None = None
    include_context: bool = True
    include_messages: bool = True
    max_context_chars: int | None = None


def _request_id_from_header(request: Request) -> str:
    """Return a validated X-Request-ID header value, generating a UUID when absent."""
    request_id = request.headers.get("x-request-id", "").strip()
    if not request_id:
        return str(uuid4())
    if len(request_id) > 128 or any(char.isspace() for char in request_id):
        raise ValueError("X-Request-ID must be 1-128 visible characters without spaces")
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


def _hash_query(query: str) -> str:
    """Return the SHA-256 hex digest of the query for redacted audit logging."""
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _auth_required(path: str) -> bool:
    """Return whether the given request path requires authentication."""
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
            "description": "Bearer token accepted by RAG middleware when API key authentication is enabled.",
        }
        security_schemes["ApiKeyAuth"] = {
            "type": "apiKey",
            "in": "header",
            "name": settings.api_key_header,
            "description": "API key header accepted by RAG middleware when API key authentication is enabled.",
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


def _write_audit_log(
    settings: Settings,
    request: Request,
    route: str,
    status_code: int,
    latency_seconds: float,
    query: str | None = None,
    result_ids: list[str] | None = None,
    error: str | None = None,
) -> None:
    """Emit a redacted JSON audit event for the request when auditing is enabled."""
    if not settings.audit_log_enabled:
        return
    event: dict[str, Any] = {
        "event": "rag_query",
        "route": route,
        "request_id": request.state.request_id,
        "traceparent": request.state.traceparent,
        "sandbox_id": request.state.sandbox_id,
        "status_code": status_code,
        "latency_ms": round(latency_seconds * 1000, 2),
        "result_ids": result_ids or [],
        "error": error,
    }
    if query is not None:
        event["query_chars"] = len(query)
        event["query_sha256"] = _hash_query(query)
    line = json.dumps(event, sort_keys=True)
    AUDIT_LOGGER.info(line)
    logging.getLogger("uvicorn.error").info(line)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Eagerly bootstrap the vector store at startup so reachability surfaces early.

    Bootstrapping is best-effort: a failure is recorded on the retriever (and
    reflected by ``/readyz``) rather than crashing startup, avoiding a cold-start
    thundering herd where every concurrent first request races to bootstrap.
    """
    bootstrap = getattr(app.state.retriever, "bootstrap", None)
    if callable(bootstrap):
        try:
            await bootstrap()
        except VectorStoreError:
            logging.getLogger("uvicorn.error").warning(
                "vector store bootstrap failed at startup; readiness will report not_ready until it recovers"
            )
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the RAG service FastAPI application with its retriever."""
    resolved = settings or Settings.from_env()
    app = FastAPI(
        title="Private AI Platform Kit RAG Service",
        version=SERVICE_VERSION,
        description=OPENAPI_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs",
        redoc_url=None,
        lifespan=_lifespan,
    )
    app.state.settings = resolved
    if resolved.retrieval_backend == "qdrant":
        embedding_provider = build_embedding_provider(
            resolved.embedding_provider,
            resolved.vector_dimensions,
            resolved.embedding_model,
            resolved.embedding_base_url,
            resolved.vector_timeout_seconds,
        )
        app.state.retriever = QdrantRetriever.from_directory(
            resolved.document_dir,
            resolved.vector_store_url,
            resolved.vector_collection,
            resolved.vector_collection_version,
            resolved.vector_timeout_seconds,
            resolved.vector_dimensions,
            resolved.vector_bootstrap_enabled,
            embedding_provider,
        )
    else:
        app.state.retriever = LexicalRetriever.from_directory(resolved.document_dir)
    tracing = configure_tracing(resolved)
    app.state.tracer = tracing[0] if tracing else None
    app.state.tracer_provider = tracing[1] if tracing else None

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

        async def dispatch() -> Response:
            if (
                resolved.api_key_auth_enabled
                and _auth_required(request.url.path)
                and not _valid_api_key(request, resolved)
            ):
                return _auth_failure_response(request, "invalid_or_missing_api_key")
            response = await call_next(request)
            response.headers["X-Request-ID"] = request.state.request_id
            response.headers["X-Sandbox-ID"] = request.state.sandbox_id
            if request.state.traceparent:
                response.headers["traceparent"] = request.state.traceparent
            return response

        tracer = request.app.state.tracer
        if tracer is None:
            return await dispatch()
        return await trace_request(tracer, request, dispatch)

    @app.get(
        "/healthz",
        tags=["health"],
        summary="Check RAG service readiness",
        operation_id="getRagHealth",
    )
    async def healthz() -> dict[str, Any]:
        REQUESTS.labels("/healthz", "200").inc()
        body: dict[str, Any] = {
            "status": "ok",
            "documents": len(app.state.retriever.documents),
            "retrieval_backend": resolved.retrieval_backend,
            "vector_store_configured": bool(resolved.vector_store_url),
            "source_manifest_configured": resolved.rag_source_manifest is not None,
        }
        if resolved.rag_source_manifest is not None:
            body["source_manifest"] = str(resolved.rag_source_manifest)
        status = getattr(app.state.retriever, "status", None)
        if callable(status):
            body["vector_store"] = status()
        return body

    @app.get(
        "/readyz",
        tags=["health"],
        summary="Check RAG service and vector store readiness",
        operation_id="getRagReadiness",
        include_in_schema=False,
    )
    async def readyz(response: Response) -> dict[str, Any]:
        ready = True
        vector_store: dict[str, Any] = {"status": "ok"}
        if resolved.retrieval_backend == "qdrant":
            ping = getattr(app.state.retriever, "ping", None)
            reachable = await ping() if callable(ping) else False
            ready = bool(reachable)
            vector_store = {"status": "ok" if ready else "unavailable"}
            status_obj = getattr(app.state.retriever, "status", None)
            if callable(status_obj):
                vector_store["last_sync_status"] = status_obj().get("last_sync_status")
        response.status_code = 200 if ready else 503
        REQUESTS.labels("/readyz", str(response.status_code)).inc()
        return {
            "status": "ready" if ready else "not_ready",
            "retrieval_backend": resolved.retrieval_backend,
            "vector_store": vector_store,
        }

    @app.get(
        "/metrics",
        tags=["observability"],
        summary="Export Prometheus metrics",
        operation_id="getRagMetrics",
    )
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get(
        "/v1/rag/documents",
        tags=["retrieval"],
        summary="List indexed knowledge documents",
        operation_id="listRagDocuments",
    )
    async def documents(request: Request) -> dict[str, Any]:
        route = "/v1/rag/documents"
        start = perf_counter()
        REQUESTS.labels(route, "200").inc()
        LATENCY.labels(route).observe(perf_counter() - start)
        return {
            "request_id": request.state.request_id,
            "sandbox_id": request.state.sandbox_id,
            "documents": [
                {
                    "id": document.id,
                    "title": document.title,
                    "source": document.source,
                }
                for document in app.state.retriever.documents
            ],
        }

    @app.post(
        "/v1/rag/query",
        tags=["retrieval"],
        summary="Retrieve grounded context for a query",
        operation_id="queryRagContext",
    )
    async def query(request: Request, payload: RagQueryRequest) -> dict[str, Any]:
        route = "/v1/rag/query"
        start = perf_counter()
        status = "200"
        status_code = 200
        error = None
        result_ids: list[str] = []
        query_text = payload.query.strip()
        try:
            if not query_text:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": "query must contain non-whitespace text",
                        "reason": "empty_query",
                        "request_id": request.state.request_id,
                        "sandbox_id": request.state.sandbox_id,
                    },
                )
            if len(query_text) > resolved.max_query_chars:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": "query exceeds max_query_chars",
                        "reason": "query_too_large",
                        "request_id": request.state.request_id,
                        "sandbox_id": request.state.sandbox_id,
                    },
                )
            top_k = resolved.default_top_k if payload.top_k is None else payload.top_k
            if top_k <= 0 or top_k > resolved.max_top_k:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": f"top_k must be between 1 and {resolved.max_top_k}",
                        "reason": "invalid_top_k",
                        "request_id": request.state.request_id,
                        "sandbox_id": request.state.sandbox_id,
                    },
                )
            max_context_chars = (
                resolved.max_context_chars if payload.max_context_chars is None else payload.max_context_chars
            )
            if max_context_chars <= 0 or max_context_chars > resolved.max_context_chars:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": f"max_context_chars must be between 1 and {resolved.max_context_chars}",
                        "reason": "invalid_max_context_chars",
                        "request_id": request.state.request_id,
                        "sandbox_id": request.state.sandbox_id,
                    },
                )

            results = await app.state.retriever.query(query_text, top_k, max_context_chars)
            result_ids = [result.document.id for result in results]
            context = build_context(results, max_context_chars)
            RETRIEVAL_RESULTS.labels(request.state.sandbox_id).observe(len(results))
            response: dict[str, Any] = {
                "request_id": request.state.request_id,
                "sandbox_id": request.state.sandbox_id,
                "retrieval_backend": resolved.retrieval_backend,
                "query_sha256": _hash_query(query_text),
                "results": [
                    {
                        "id": result.document.id,
                        "title": result.document.title,
                        "source": result.document.source,
                        "score": result.score,
                        "excerpt": result.excerpt,
                    }
                    for result in results
                ],
            }
            if payload.include_context:
                response["context"] = context
            if payload.include_messages:
                response["grounded_messages"] = [
                    {
                        "role": "system",
                        "content": (
                            "Use the retrieved platform context to answer. "
                            "If the context is insufficient, say what is missing. "
                            "Do not reveal hidden chain-of-thought."
                        ),
                    },
                    {
                        "role": "system",
                        "content": f"Retrieved context:\n{context}" if context else "Retrieved context: none",
                    },
                    {"role": "user", "content": query_text},
                ]
            return response
        except HTTPException as exc:
            status = str(exc.status_code)
            status_code = exc.status_code
            error = str(exc.detail)
            raise
        except VectorStoreError as exc:
            status = "503"
            status_code = 503
            error = str(exc)
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "vector store is unavailable",
                    "reason": "vector_store_unavailable",
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
            ) from exc
        finally:
            REQUESTS.labels(route, status).inc()
            latency_seconds = perf_counter() - start
            LATENCY.labels(route).observe(latency_seconds)
            _write_audit_log(
                resolved,
                request,
                route,
                status_code=status_code,
                latency_seconds=latency_seconds,
                query=query_text,
                result_ids=result_ids,
                error=error,
            )

    _install_openapi_contract(app, resolved)
    return app


app = create_app()
