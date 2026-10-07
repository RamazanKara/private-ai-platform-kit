"""Batch-processor worker: drain the queue and replay each item through the gateway (ADR 0011).

Runs as the gateway image with ``python -m app.batch_worker``. It is stateless (all state
lives in the object store and the batch store), so the Deployment scales horizontally and
restarts freely. For each claimed batch it replays every input line against the gateway's own
governed endpoint, so the model allowlist, admission caps, prompt-secret policy, budget, output
guardrail, tenant isolation, and audit chain all apply per item exactly as for live traffic.
Successful (2xx) items land in the output file; everything else lands in the error file.

Three properties keep a large or interrupted batch cheap and correct:

- **Bounded memory.** The input file is read line by line from the object store, and results
  are written in parts of ``part_lines`` items, so memory does not grow with the file.
- **Checkpointed progress.** After each part the batch record stores how many input lines
  are done and which parts exist. A restarted or reclaimed batch resumes there, so a crash
  replays (and re-charges) at most one part, not the whole file.
- **Owned claims.** Each claim carries a token. The worker refreshes it between chunks and
  stops, without finalizing or acknowledging, when another worker has taken the batch over.

Items are replayed on the submitter's behalf: the worker sends ``X-Batch-ID``, and a worker
key holding the ``batch_replay`` scope may act for a tenant only while that tenant's batch is
running, with each receipt naming the submitter. Cancellation and the completion-window expiry
are honored at chunk boundaries.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import signal
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from time import time
from typing import Any

import httpx

from app.batchstore import (
    BATCH_CANCELLED,
    BATCH_CANCELLING,
    BATCH_COMPLETED,
    BATCH_EXPIRED,
    BATCH_FAILED,
    BATCH_FINALIZING,
    BATCH_IN_PROGRESS,
    BatchRecord,
    BatchStore,
    Claim,
    FileRecord,
    build_batch_store,
)
from app.objectstore import ObjectNotFound, ObjectStore, build_object_store
from app.settings import Settings

_LOGGER = logging.getLogger("ai_platform_ops_lab.batch_worker")
_TERMINAL = frozenset({BATCH_COMPLETED, BATCH_FAILED, BATCH_EXPIRED, BATCH_CANCELLED})


@dataclass(frozen=True)
class WorkerConfig:
    """Worker-only runtime config (read directly from env, not part of the gateway Settings)."""

    gateway_url: str
    api_key: str
    api_key_header: str
    concurrency: int
    poll_seconds: float
    reclaim_seconds: float
    request_timeout: float
    part_lines: int = 1000

    @classmethod
    def from_env(cls) -> WorkerConfig:
        return cls(
            gateway_url=os.getenv(
                "BATCH_WORKER_GATEWAY_URL", "http://inference-gateway.inference.svc.cluster.local:8080"
            ).rstrip("/"),
            api_key=os.getenv("BATCH_WORKER_API_KEY", ""),
            api_key_header=os.getenv("API_KEY_HEADER", "X-API-Key"),
            concurrency=max(1, int(os.getenv("BATCH_WORKER_CONCURRENCY", "4"))),
            poll_seconds=max(0.1, float(os.getenv("BATCH_WORKER_POLL_SECONDS", "2"))),
            reclaim_seconds=max(1.0, float(os.getenv("BATCH_WORKER_RECLAIM_SECONDS", "300"))),
            request_timeout=max(1.0, float(os.getenv("BATCH_WORKER_REQUEST_TIMEOUT_SECONDS", "120"))),
            part_lines=max(1, int(os.getenv("BATCH_WORKER_PART_LINES", "1000"))),
        )


class ClaimLost(Exception):
    """This worker's queue claim was reclaimed; another replica now owns the batch."""


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(16)}"


def _parse_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


async def _replay_line(
    client: httpx.AsyncClient, config: WorkerConfig, record: BatchRecord, line: str
) -> dict[str, Any]:
    """Replay one JSONL request line through the gateway; return an output/error result dict.

    The result carries an ``__error__`` flag the caller uses to route it to the output or error
    file. Malformed lines and endpoint mismatches fail the item without a network call.
    """
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return _error_item(None, "invalid_json", "request line is not valid JSON")
    if not isinstance(obj, dict):
        # Valid JSON that is not an object ([1], "x") must fail this item, not the batch.
        return _error_item(None, "invalid_json", "request line must be a JSON object")
    custom_id = obj.get("custom_id")
    body = obj.get("body")
    url = obj.get("url", record.endpoint)
    if url != record.endpoint:
        return _error_item(custom_id, "endpoint_mismatch", f"line url '{url}' does not match batch endpoint")
    if not isinstance(body, dict):
        return _error_item(custom_id, "invalid_body", "request line 'body' must be a JSON object")
    headers = {"X-Sandbox-ID": record.tenant, "X-Batch-ID": record.id, "Content-Type": "application/json"}
    headers["X-Data-Classification"] = record.data_classification
    if config.api_key:
        headers[config.api_key_header] = config.api_key
    try:
        response = await client.post(
            f"{config.gateway_url}{record.endpoint}", json=body, headers=headers, timeout=config.request_timeout
        )
    except httpx.HTTPError as exc:
        return _error_item(custom_id, "request_failed", f"gateway request failed: {type(exc).__name__}")
    item = {
        "id": _new_id("batch_req"),
        "custom_id": custom_id,
        "response": {"status_code": response.status_code, "body": _parse_body(response)},
        "error": None,
    }
    if response.status_code >= 400:
        item["error"] = {"message": f"item returned status {response.status_code}"}
        item["__error__"] = True
    return item


def _error_item(custom_id: Any, code: str, message: str) -> dict[str, Any]:
    return {
        "id": _new_id("batch_req"),
        "custom_id": custom_id,
        "response": None,
        "error": {"code": code, "message": message},
        "__error__": True,
    }


def _jsonl(items: list[dict[str, Any]]) -> bytes:
    body = "\n".join(json.dumps({k: v for k, v in item.items() if k != "__error__"}) for item in items)
    return (body + "\n").encode("utf-8") if body else b""


def _part_key(tenant: str, batch_id: str, kind: str, index: int) -> str:
    return f"{tenant}/parts/{batch_id}/{kind}-{index:05d}.jsonl"


def _input_lines(object_store: ObjectStore, object_key: str, skip: int) -> Iterator[str]:
    """Yield the non-blank input lines after the first ``skip`` of them, streaming the object."""
    seen = 0
    for raw in object_store.open_lines(object_key):
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        seen += 1
        if seen > skip:
            yield line


async def process_batch(
    config: WorkerConfig,
    object_store: ObjectStore,
    batch_store: BatchStore,
    tenant: str,
    batch_id: str,
    client: httpx.AsyncClient,
    *,
    claim: Claim | None = None,
) -> None:
    """Process one batch to a terminal state, resuming from its last checkpoint.

    With a ``claim`` the worker refreshes it between chunks and raises :class:`ClaimLost`,
    without finalizing, if another worker has taken the batch over.
    """
    record = batch_store.get_batch(tenant, batch_id)
    if record is None or record.status in _TERMINAL:
        return
    now = int(time())
    if record.status == BATCH_CANCELLING:
        batch_store.update_batch(tenant, batch_id, {"status": BATCH_CANCELLED, "cancelled_at": now})
        return
    if now > record.expires_at:
        batch_store.update_batch(tenant, batch_id, {"status": BATCH_EXPIRED, "expired_at": now})
        return

    in_progress: dict[str, Any] = {"status": BATCH_IN_PROGRESS}
    if record.in_progress_at is None:
        in_progress["in_progress_at"] = now
    record = batch_store.update_batch(tenant, batch_id, in_progress) or record
    file_record = batch_store.get_file(tenant, record.input_file_id)
    if file_record is None:
        _fail(batch_store, tenant, batch_id, "input file record is missing")
        return

    progress = _Progress.from_record(record)
    outputs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    cancelled = expired = False
    try:
        lines = _input_lines(object_store, file_record.object_key, record.processed_lines)
        chunk: list[str] = []
        exhausted = False
        while not exhausted:
            chunk.clear()
            for line in lines:
                chunk.append(line)
                if len(chunk) >= config.concurrency:
                    break
            exhausted = len(chunk) < config.concurrency
            if not chunk:
                break
            if claim is not None and not batch_store.heartbeat(claim):
                raise ClaimLost(batch_id)
            current = batch_store.get_batch(tenant, batch_id)
            if current is not None and current.status == BATCH_CANCELLING:
                cancelled = True
                break
            if int(time()) > record.expires_at:
                expired = True
                break
            for item in await asyncio.gather(*(_replay_line(client, config, record, line) for line in chunk)):
                (errors if item.pop("__error__", False) else outputs).append(item)
            progress.processed += len(chunk)
            if len(outputs) + len(errors) >= config.part_lines:
                _checkpoint(object_store, batch_store, tenant, batch_id, progress, outputs, errors)
    except ObjectNotFound:
        _fail(batch_store, tenant, batch_id, "input file content is missing")
        return
    # Results gathered before a cancel or expiry are kept, as in OpenAI's batch semantics.
    _checkpoint(object_store, batch_store, tenant, batch_id, progress, outputs, errors)
    _finalize(object_store, batch_store, record, tenant, batch_id, progress, cancelled, expired)


@dataclass
class _Progress:
    processed: int
    completed: int
    failed: int
    output_parts: int
    error_parts: int

    @classmethod
    def from_record(cls, record: BatchRecord) -> _Progress:
        return cls(record.processed_lines, record.completed, record.failed, record.output_parts, record.error_parts)


def _checkpoint(
    object_store: ObjectStore,
    batch_store: BatchStore,
    tenant: str,
    batch_id: str,
    progress: _Progress,
    outputs: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    """Write buffered results as new parts, then record the progress that covers them.

    The part objects are written before the record moves forward: a crash in between leaves
    an orphaned part that the resume overwrites, never a record pointing at a missing part.
    """
    if outputs:
        object_store.put(_part_key(tenant, batch_id, "output", progress.output_parts), _jsonl(outputs))
        progress.output_parts += 1
        progress.completed += len(outputs)
    if errors:
        object_store.put(_part_key(tenant, batch_id, "error", progress.error_parts), _jsonl(errors))
        progress.error_parts += 1
        progress.failed += len(errors)
    outputs.clear()
    errors.clear()
    batch_store.update_batch(
        tenant,
        batch_id,
        {
            "processed_lines": progress.processed,
            "completed": progress.completed,
            "failed": progress.failed,
            "output_parts": progress.output_parts,
            "error_parts": progress.error_parts,
        },
    )


def _finalize(
    object_store: ObjectStore,
    batch_store: BatchStore,
    record: BatchRecord,
    tenant: str,
    batch_id: str,
    progress: _Progress,
    cancelled: bool,
    expired: bool,
) -> None:
    now = int(time())
    batch_store.update_batch(tenant, batch_id, {"status": BATCH_FINALIZING, "finalizing_at": now})
    updates: dict[str, Any] = {"completed": progress.completed, "failed": progress.failed}
    # Deterministic file ids keyed by batch id keep re-finalizing idempotent (overwrite, not append).
    if progress.output_parts:
        updates["output_file_id"] = _assemble_result_file(
            object_store, batch_store, tenant, batch_id, "output", "batch_output", progress.output_parts
        )
    if progress.error_parts:
        updates["error_file_id"] = _assemble_result_file(
            object_store, batch_store, tenant, batch_id, "error", "batch_error", progress.error_parts
        )
    if cancelled:
        updates.update({"status": BATCH_CANCELLED, "cancelled_at": now})
    elif expired:
        updates.update({"status": BATCH_EXPIRED, "expired_at": now})
    else:
        updates.update({"status": BATCH_COMPLETED, "completed_at": now})
    batch_store.update_batch(tenant, batch_id, updates)


def _assemble_result_file(
    object_store: ObjectStore,
    batch_store: BatchStore,
    tenant: str,
    batch_id: str,
    kind: str,
    purpose: str,
    parts: int,
) -> str:
    """Concatenate result parts into the downloadable file through a spooled temp file."""
    file_id = f"file-{kind}-{batch_id}"
    object_key = f"{tenant}/{file_id}"
    lines = 0
    with tempfile.TemporaryFile() as spool:
        for index in range(parts):
            for line in object_store.open_lines(_part_key(tenant, batch_id, kind, index)):
                spool.write(line)
                lines += 1
        size = spool.tell()
        object_store.put_stream(object_key, spool, size)
    batch_store.create_file(
        FileRecord(
            id=file_id,
            tenant=tenant,
            bytes=size,
            created_at=int(time()),
            filename=f"{batch_id}-{kind}.jsonl",
            purpose=purpose,
            object_key=object_key,
            line_count=lines,
        )
    )
    for index in range(parts):
        object_store.delete(_part_key(tenant, batch_id, kind, index))
    return file_id


def _fail(batch_store: BatchStore, tenant: str, batch_id: str, reason: str) -> None:
    batch_store.update_batch(tenant, batch_id, {"status": BATCH_FAILED, "failed_at": int(time()), "error": reason})


async def run_once(
    config: WorkerConfig, object_store: ObjectStore, batch_store: BatchStore, client: httpx.AsyncClient
) -> bool:
    """Claim and process one batch; return False when the queue was empty."""
    claim = batch_store.claim()
    if claim is None:
        return False
    try:
        await process_batch(config, object_store, batch_store, claim.tenant, claim.batch_id, client, claim=claim)
    except ClaimLost:
        # The batch now belongs to another worker. The token-checked ack below would be a
        # no-op anyway; returning early just avoids the misleading attempt.
        _LOGGER.warning("batch %s was taken over by another worker; stopping without finalizing", claim.batch_id)
        return True
    except Exception as exc:
        _LOGGER.exception("batch %s processing failed", claim.batch_id)
        _fail(batch_store, claim.tenant, claim.batch_id, f"worker error: {type(exc).__name__}")
    # Not in a finally: a worker cancelled mid-batch must leave its claim behind so the
    # reaper re-queues the batch instead of it sitting in_progress forever.
    batch_store.ack(claim)
    return True


async def run_worker(
    config: WorkerConfig, object_store: ObjectStore, batch_store: BatchStore, stop: asyncio.Event
) -> None:
    """Main loop: reclaim stale batches, then claim/process until asked to stop."""
    last_reclaim = 0.0
    async with httpx.AsyncClient() as client:
        while not stop.is_set():
            now = time()
            if now - last_reclaim >= config.reclaim_seconds:
                reclaimed = batch_store.reclaim(config.reclaim_seconds)
                if reclaimed:
                    _LOGGER.info("re-queued %d stale batch(es)", reclaimed)
                last_reclaim = now
            if not await run_once(config, object_store, batch_store, client):
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=config.poll_seconds)


async def _amain() -> None:
    settings = Settings.from_env()
    if not settings.batch_api_enabled:
        _LOGGER.error("BATCH_API_ENABLED is false; the batch-processor has nothing to do")
        return
    config = WorkerConfig.from_env()
    object_store = build_object_store(settings)
    batch_store = build_batch_store(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # pragma: no cover - not on all platforms
            loop.add_signal_handler(sig, stop.set)
    _LOGGER.info("batch-processor started; gateway=%s", config.gateway_url)
    await run_worker(config, object_store, batch_store, stop)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
