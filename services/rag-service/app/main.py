from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

from app.retriever import LexicalRetriever, QdrantRetriever, VectorStoreError, build_context
from app.settings import Settings, validate_sandbox_id


AUDIT_LOGGER = logging.getLogger("ai_platform_ops_lab.rag.audit")
TRACEPARENT_PATTERN = re.compile(r"^[\da-f]{2}-[\da-f]{32}-[\da-f]{16}-[\da-f]{2}$")

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
    query: str = Field(..., min_length=1)
    top_k: int | None = None
    include_context: bool = True
    include_messages: bool = True
    max_context_chars: int | None = None


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


def _hash_query(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


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


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    app = FastAPI(
        title="AI Platform Ops Lab RAG Service",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.settings = resolved
    if resolved.retrieval_backend == "qdrant":
        app.state.retriever = QdrantRetriever.from_directory(
            resolved.document_dir,
            resolved.vector_store_url,
            resolved.vector_collection,
            resolved.vector_timeout_seconds,
            resolved.vector_dimensions,
            resolved.vector_bootstrap_enabled,
        )
    else:
        app.state.retriever = LexicalRetriever.from_directory(resolved.document_dir)

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
    async def healthz() -> dict[str, Any]:
        REQUESTS.labels("/healthz", "200").inc()
        body: dict[str, Any] = {
            "status": "ok",
            "documents": len(app.state.retriever.documents),
            "retrieval_backend": resolved.retrieval_backend,
            "vector_store_configured": bool(resolved.vector_store_url),
        }
        status = getattr(app.state.retriever, "status", None)
        if callable(status):
            body["vector_store"] = status()
        return body

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/rag/documents")
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

    @app.post("/v1/rag/query")
    async def query(request: Request, payload: RagQueryRequest) -> dict[str, Any]:
        route = "/v1/rag/query"
        start = perf_counter()
        status = "200"
        status_code = 200
        error = None
        result_ids: list[str] = []
        query_text = payload.query.strip()
        try:
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
            top_k = payload.top_k or resolved.default_top_k
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
            max_context_chars = payload.max_context_chars or resolved.max_context_chars
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

            results = app.state.retriever.query(query_text, top_k, max_context_chars)
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

    return app


app = create_app()
