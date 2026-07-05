"""Batch-processor worker: drain the queue and replay each item through the gateway (ADR 0011).

Runs as the gateway image with ``python -m app.batch_worker``. It is stateless (all state
lives in the object store and the batch store), so the Deployment scales horizontally and
restarts freely. For each claimed batch it replays every input line against the gateway's own
governed endpoint, so the model allowlist, admission caps, prompt-secret policy, budget, output
guardrail, tenant isolation, and audit chain all apply per item exactly as for live traffic.
Successful (2xx) items land in the output file; everything else lands in the error file.
Cancellation and the completion-window expiry are honored at item boundaries.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import signal
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
        )


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
    custom_id = obj.get("custom_id")
    body = obj.get("body")
    url = obj.get("url", record.endpoint)
    if url != record.endpoint:
        return _error_item(custom_id, "endpoint_mismatch", f"line url '{url}' does not match batch endpoint")
    if not isinstance(body, dict):
        return _error_item(custom_id, "invalid_body", "request line 'body' must be a JSON object")
    headers = {"X-Sandbox-ID": record.tenant, "Content-Type": "application/json"}
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


async def process_batch(
    config: WorkerConfig,
    object_store: ObjectStore,
    batch_store: BatchStore,
    tenant: str,
    batch_id: str,
    client: httpx.AsyncClient,
) -> None:
    """Process one claimed batch to a terminal state (idempotent: safe to re-run)."""
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

    batch_store.update_batch(tenant, batch_id, {"status": BATCH_IN_PROGRESS, "in_progress_at": now})
    file_record = batch_store.get_file(tenant, record.input_file_id)
    if file_record is None:
        _fail(batch_store, tenant, batch_id, "input file record is missing")
        return
    try:
        data = object_store.get(file_record.object_key)
    except ObjectNotFound:
        _fail(batch_store, tenant, batch_id, "input file content is missing")
        return
    lines = [line for line in data.decode("utf-8", errors="replace").splitlines() if line.strip()]

    outputs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    cancelled = False
    for start in range(0, len(lines), config.concurrency):
        current = batch_store.get_batch(tenant, batch_id)
        if current is not None and current.status == BATCH_CANCELLING:
            cancelled = True
            break
        chunk = lines[start : start + config.concurrency]
        for item in await asyncio.gather(*(_replay_line(client, config, record, line) for line in chunk)):
            (errors if item.pop("__error__", False) else outputs).append(item)
        batch_store.update_batch(tenant, batch_id, {"completed": len(outputs), "failed": len(errors)})

    _finalize(object_store, batch_store, record, tenant, batch_id, outputs, errors, cancelled)


def _finalize(
    object_store: ObjectStore,
    batch_store: BatchStore,
    record: BatchRecord,
    tenant: str,
    batch_id: str,
    outputs: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    cancelled: bool,
) -> None:
    now = int(time())
    batch_store.update_batch(tenant, batch_id, {"status": BATCH_FINALIZING, "finalizing_at": now})
    updates: dict[str, Any] = {"completed": len(outputs), "failed": len(errors)}
    # Deterministic file ids keyed by batch id keep re-processing idempotent (overwrite, not append).
    if outputs:
        updates["output_file_id"] = _write_result_file(
            object_store, batch_store, tenant, batch_id, "output", "batch_output", outputs
        )
    if errors:
        updates["error_file_id"] = _write_result_file(
            object_store, batch_store, tenant, batch_id, "error", "batch_error", errors
        )
    if cancelled:
        updates.update({"status": BATCH_CANCELLED, "cancelled_at": now})
    else:
        updates.update({"status": BATCH_COMPLETED, "completed_at": now})
    batch_store.update_batch(tenant, batch_id, updates)


def _write_result_file(
    object_store: ObjectStore,
    batch_store: BatchStore,
    tenant: str,
    batch_id: str,
    kind: str,
    purpose: str,
    items: list[dict[str, Any]],
) -> str:
    file_id = f"file-{kind}-{batch_id}"
    object_key = f"{tenant}/{file_id}"
    blob = _jsonl(items)
    object_store.put(object_key, blob)
    batch_store.create_file(
        FileRecord(
            id=file_id,
            tenant=tenant,
            bytes=len(blob),
            created_at=int(time()),
            filename=f"{batch_id}-{kind}.jsonl",
            purpose=purpose,
            object_key=object_key,
            line_count=len(items),
        )
    )
    return file_id


def _fail(batch_store: BatchStore, tenant: str, batch_id: str, reason: str) -> None:
    batch_store.update_batch(tenant, batch_id, {"status": BATCH_FAILED, "failed_at": int(time()), "error": reason})


async def run_once(
    config: WorkerConfig, object_store: ObjectStore, batch_store: BatchStore, client: httpx.AsyncClient
) -> bool:
    """Claim and process one batch; return False when the queue was empty."""
    claimed = batch_store.claim()
    if claimed is None:
        return False
    tenant, batch_id = claimed
    try:
        await process_batch(config, object_store, batch_store, tenant, batch_id, client)
    except Exception as exc:
        _LOGGER.exception("batch %s processing failed", batch_id)
        _fail(batch_store, tenant, batch_id, f"worker error: {type(exc).__name__}")
    finally:
        batch_store.ack(tenant, batch_id)
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
