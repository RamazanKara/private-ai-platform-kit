"""OpenAI-compatible asynchronous Files + Batch API routes (ADR 0011).

These routes are served by the gateway; the separate ``batch-processor`` worker drains the
queue and replays each item back through the gateway's governed inference endpoints, so all
policy stays single-sourced. The routes are always registered so the OpenAPI contract is
stable, but every handler returns ``404`` unless ``BATCH_API_ENABLED`` is set.

Every handler is tenant-scoped: the object/record key is prefixed with the caller's resolved
sandbox id (``request.state.sandbox_id``, which JWT tenant binding pins), so one tenant can
neither read nor delete another tenant's files or batches.
"""

from __future__ import annotations

import secrets
from time import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from app.batchstore import BatchRecord, FileRecord
from app.objectstore import ObjectNotFound
from app.settings import BATCH_ALLOWED_ENDPOINTS, Settings, parse_completion_window

_FILE_PURPOSES = frozenset({"batch"})
_MAX_METADATA_PAIRS = 16


class CreateBatchRequest(BaseModel):
    """Body of POST /v1/batches."""

    input_file_id: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    completion_window: str = "24h"
    metadata: dict[str, str] | None = None


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(16)}"


def _tenant(request: Request) -> str:
    return request.state.sandbox_id


def _error(status: int, reason: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"reason": reason, "message": message})


def _require_enabled(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    if not settings.batch_api_enabled:
        raise _error(404, "batch_api_disabled", "the asynchronous batch API is not enabled")
    return settings


def _decode_jsonl(data: bytes) -> list[str]:
    """Return the non-empty lines of a UTF-8 JSONL blob, or raise a 400 on bad encoding."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _error(400, "invalid_encoding", "input file must be UTF-8 encoded") from exc
    return [line for line in text.splitlines() if line.strip()]


def register_batch_routes(app: FastAPI, settings: Settings) -> None:
    """Register the /v1/files and /v1/batches routes on the app (always, gated at runtime)."""

    @app.post("/v1/files", tags=["files"], summary="Upload a JSONL file (purpose=batch)", operation_id="createFile")
    async def create_file(request: Request) -> JSONResponse:
        resolved = _require_enabled(request)
        # Parse the multipart form directly (rather than File()/Form() defaults) to match the
        # gateway's request-parsing style; python-multipart powers request.form().
        form = await request.form()
        upload = form.get("file")
        purpose = form.get("purpose")
        if not isinstance(upload, UploadFile):
            raise _error(400, "missing_file", "a multipart 'file' field is required")
        if not isinstance(purpose, str) or purpose not in _FILE_PURPOSES:
            raise _error(400, "invalid_purpose", "purpose must be 'batch'")
        data = await upload.read()
        if len(data) > resolved.batch_max_file_bytes:
            raise _error(413, "file_too_large", f"file exceeds the {resolved.batch_max_file_bytes}-byte limit")
        lines = _decode_jsonl(data)
        if not lines:
            raise _error(400, "empty_file", "input file has no request lines")
        if len(lines) > resolved.batch_max_requests_per_batch:
            raise _error(
                400,
                "too_many_requests",
                f"file has {len(lines)} lines; limit is {resolved.batch_max_requests_per_batch}",
            )
        tenant = _tenant(request)
        file_id = _new_id("file")
        object_key = f"{tenant}/{file_id}"
        request.app.state.object_store.put(object_key, data)
        record = FileRecord(
            id=file_id,
            tenant=tenant,
            bytes=len(data),
            created_at=int(time()),
            filename=upload.filename or "input.jsonl",
            purpose=purpose,
            object_key=object_key,
            line_count=len(lines),
        )
        request.app.state.batch_store.create_file(record)
        return JSONResponse(status_code=200, content=record.to_public())

    @app.get("/v1/files", tags=["files"], summary="List uploaded files", operation_id="listFiles")
    async def list_files(request: Request) -> dict[str, Any]:
        _require_enabled(request)
        records = request.app.state.batch_store.list_files(_tenant(request))
        return {"object": "list", "data": [r.to_public() for r in records]}

    @app.get("/v1/files/{file_id}", tags=["files"], summary="Retrieve a file object", operation_id="getFile")
    async def get_file(request: Request, file_id: str) -> dict[str, Any]:
        _require_enabled(request)
        record = request.app.state.batch_store.get_file(_tenant(request), file_id)
        if record is None:
            raise _error(404, "file_not_found", f"no file with id '{file_id}'")
        return record.to_public()

    @app.get(
        "/v1/files/{file_id}/content", tags=["files"], summary="Download file content", operation_id="getFileContent"
    )
    async def get_file_content(request: Request, file_id: str) -> Response:
        _require_enabled(request)
        record = request.app.state.batch_store.get_file(_tenant(request), file_id)
        if record is None:
            raise _error(404, "file_not_found", f"no file with id '{file_id}'")
        try:
            data = request.app.state.object_store.get(record.object_key)
        except ObjectNotFound as exc:
            raise _error(404, "file_content_missing", "file content is no longer available") from exc
        return Response(content=data, media_type="application/jsonl")

    @app.delete("/v1/files/{file_id}", tags=["files"], summary="Delete a file", operation_id="deleteFile")
    async def delete_file(request: Request, file_id: str) -> dict[str, Any]:
        _require_enabled(request)
        tenant = _tenant(request)
        record = request.app.state.batch_store.get_file(tenant, file_id)
        if record is None:
            raise _error(404, "file_not_found", f"no file with id '{file_id}'")
        request.app.state.object_store.delete(record.object_key)
        request.app.state.batch_store.delete_file(tenant, file_id)
        return {"id": file_id, "object": "file", "deleted": True}

    @app.post("/v1/batches", tags=["batches"], summary="Create an asynchronous batch", operation_id="createBatch")
    async def create_batch(request: Request, payload: CreateBatchRequest) -> JSONResponse:
        _require_enabled(request)
        if payload.endpoint not in BATCH_ALLOWED_ENDPOINTS:
            raise _error(
                400,
                "invalid_endpoint",
                f"endpoint '{payload.endpoint}' is not batchable; allowed: {sorted(BATCH_ALLOWED_ENDPOINTS)}",
            )
        try:
            window_seconds = parse_completion_window(payload.completion_window)
        except ValueError as exc:
            raise _error(400, "invalid_completion_window", str(exc)) from exc
        metadata = payload.metadata or {}
        if len(metadata) > _MAX_METADATA_PAIRS:
            raise _error(400, "invalid_metadata", f"metadata has more than {_MAX_METADATA_PAIRS} keys")
        tenant = _tenant(request)
        file_record = request.app.state.batch_store.get_file(tenant, payload.input_file_id)
        if file_record is None:
            raise _error(404, "input_file_not_found", f"no file with id '{payload.input_file_id}'")
        if file_record.purpose != "batch":
            raise _error(400, "invalid_input_file", "input_file_id must reference a file uploaded with purpose 'batch'")
        now = int(time())
        batch_id = _new_id("batch")
        record = BatchRecord(
            id=batch_id,
            tenant=tenant,
            endpoint=payload.endpoint,
            input_file_id=payload.input_file_id,
            completion_window=payload.completion_window,
            created_at=now,
            expires_at=now + window_seconds,
            metadata=metadata,
            total=file_record.line_count,
        )
        request.app.state.batch_store.create_batch(record)
        request.app.state.batch_store.enqueue(tenant, batch_id)
        return JSONResponse(status_code=200, content=record.to_public())

    @app.get("/v1/batches", tags=["batches"], summary="List batches", operation_id="listBatches")
    async def list_batches(request: Request, limit: int = 20, after: str | None = None) -> dict[str, Any]:
        _require_enabled(request)
        limit = max(1, min(limit, 100))
        records = request.app.state.batch_store.list_batches(_tenant(request), limit=limit, after=after)
        data = [r.to_public() for r in records]
        return {
            "object": "list",
            "data": data,
            "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None,
            "has_more": len(records) == limit,
        }

    @app.get("/v1/batches/{batch_id}", tags=["batches"], summary="Retrieve a batch", operation_id="getBatch")
    async def get_batch(request: Request, batch_id: str) -> dict[str, Any]:
        _require_enabled(request)
        record = request.app.state.batch_store.get_batch(_tenant(request), batch_id)
        if record is None:
            raise _error(404, "batch_not_found", f"no batch with id '{batch_id}'")
        return record.to_public()

    @app.post("/v1/batches/{batch_id}/cancel", tags=["batches"], summary="Cancel a batch", operation_id="cancelBatch")
    async def cancel_batch(request: Request, batch_id: str) -> dict[str, Any]:
        _require_enabled(request)
        record = request.app.state.batch_store.cancel_batch(_tenant(request), batch_id)
        if record is None:
            raise _error(404, "batch_not_found", f"no batch with id '{batch_id}'")
        return record.to_public()
