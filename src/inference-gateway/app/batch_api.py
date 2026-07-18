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

import asyncio
import codecs
import secrets
from time import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from app.admission import BATCH_ALLOWED_ENDPOINTS
from app.batchstore import BatchRecord, FileRecord
from app.env_config import parse_completion_window
from app.objectstore import ObjectNotFound
from app.settings import Settings

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


async def _inspect_jsonl_upload(upload: UploadFile, max_bytes: int) -> tuple[int, int]:
    """Validate and count a JSONL upload incrementally, then rewind it.

    ``UploadFile`` is already backed by Starlette's spooled file. Reading bounded
    chunks avoids materializing a second full-size bytes object before the object
    store persists it.
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    pending = ""
    byte_count = 0
    line_count = 0
    try:
        while chunk := await upload.read(1024 * 1024):
            byte_count += len(chunk)
            if byte_count > max_bytes:
                raise _error(413, "file_too_large", f"file exceeds the {max_bytes}-byte limit")
            pending += decoder.decode(chunk)
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                if line.strip():
                    line_count += 1
        pending += decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise _error(400, "invalid_encoding", "input file must be UTF-8 encoded") from exc
    finally:
        await upload.seek(0)
    if pending.strip():
        line_count += 1
    return byte_count, line_count


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
        byte_count, line_count = await _inspect_jsonl_upload(upload, resolved.batch_max_file_bytes)
        if not line_count:
            raise _error(400, "empty_file", "input file has no request lines")
        if line_count > resolved.batch_max_requests_per_batch:
            raise _error(
                400,
                "too_many_requests",
                f"file has {line_count} lines; limit is {resolved.batch_max_requests_per_batch}",
            )
        tenant = _tenant(request)
        file_id = _new_id("file")
        object_key = f"{tenant}/{file_id}"
        await asyncio.to_thread(request.app.state.object_store.put_stream, object_key, upload.file, byte_count)
        record = FileRecord(
            id=file_id,
            tenant=tenant,
            bytes=byte_count,
            created_at=int(time()),
            filename=upload.filename or "input.jsonl",
            purpose=purpose,
            object_key=object_key,
            line_count=line_count,
        )
        try:
            await asyncio.to_thread(request.app.state.batch_store.create_file, record)
        except Exception:
            await asyncio.to_thread(request.app.state.object_store.delete, object_key)
            raise
        return JSONResponse(status_code=200, content=record.to_public())

    @app.get("/v1/files", tags=["files"], summary="List uploaded files", operation_id="listFiles")
    async def list_files(request: Request) -> dict[str, Any]:
        _require_enabled(request)
        records = await asyncio.to_thread(request.app.state.batch_store.list_files, _tenant(request))
        return {"object": "list", "data": [r.to_public() for r in records]}

    @app.get("/v1/files/{file_id}", tags=["files"], summary="Retrieve a file object", operation_id="getFile")
    async def get_file(request: Request, file_id: str) -> dict[str, Any]:
        _require_enabled(request)
        record = await asyncio.to_thread(request.app.state.batch_store.get_file, _tenant(request), file_id)
        if record is None:
            raise _error(404, "file_not_found", f"no file with id '{file_id}'")
        return record.to_public()

    @app.get(
        "/v1/files/{file_id}/content", tags=["files"], summary="Download file content", operation_id="getFileContent"
    )
    async def get_file_content(request: Request, file_id: str) -> Response:
        _require_enabled(request)
        record = await asyncio.to_thread(request.app.state.batch_store.get_file, _tenant(request), file_id)
        if record is None:
            raise _error(404, "file_not_found", f"no file with id '{file_id}'")
        try:
            data = await asyncio.to_thread(request.app.state.object_store.get, record.object_key)
        except ObjectNotFound as exc:
            raise _error(404, "file_content_missing", "file content is no longer available") from exc
        return Response(content=data, media_type="application/jsonl")

    @app.delete("/v1/files/{file_id}", tags=["files"], summary="Delete a file", operation_id="deleteFile")
    async def delete_file(request: Request, file_id: str) -> dict[str, Any]:
        _require_enabled(request)
        tenant = _tenant(request)
        record = await asyncio.to_thread(request.app.state.batch_store.get_file, tenant, file_id)
        if record is None:
            raise _error(404, "file_not_found", f"no file with id '{file_id}'")
        await asyncio.to_thread(request.app.state.object_store.delete, record.object_key)
        await asyncio.to_thread(request.app.state.batch_store.delete_file, tenant, file_id)
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
        file_record = await asyncio.to_thread(request.app.state.batch_store.get_file, tenant, payload.input_file_id)
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
        await asyncio.to_thread(request.app.state.batch_store.create_and_enqueue, record)
        return JSONResponse(status_code=200, content=record.to_public())

    @app.get("/v1/batches", tags=["batches"], summary="List batches", operation_id="listBatches")
    async def list_batches(request: Request, limit: int = 20, after: str | None = None) -> dict[str, Any]:
        _require_enabled(request)
        limit = max(1, min(limit, 100))
        records = await asyncio.to_thread(
            request.app.state.batch_store.list_batches, _tenant(request), limit=limit, after=after
        )
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
        record = await asyncio.to_thread(request.app.state.batch_store.get_batch, _tenant(request), batch_id)
        if record is None:
            raise _error(404, "batch_not_found", f"no batch with id '{batch_id}'")
        return record.to_public()

    @app.post("/v1/batches/{batch_id}/cancel", tags=["batches"], summary="Cancel a batch", operation_id="cancelBatch")
    async def cancel_batch(request: Request, batch_id: str) -> dict[str, Any]:
        _require_enabled(request)
        record = await asyncio.to_thread(request.app.state.batch_store.cancel_batch, _tenant(request), batch_id)
        if record is None:
            raise _error(404, "batch_not_found", f"no batch with id '{batch_id}'")
        return record.to_public()
