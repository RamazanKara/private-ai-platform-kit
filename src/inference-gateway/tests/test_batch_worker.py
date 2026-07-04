"""Tests for the batch-processor worker (ADR 0011)."""

from __future__ import annotations

import asyncio
import json
from time import time

import httpx
from app.batch_worker import WorkerConfig, _amain, process_batch, run_once, run_worker
from app.batchstore import BatchRecord, FileRecord, MemoryBatchStore
from app.objectstore import MemoryObjectStore

_CONFIG = WorkerConfig(
    gateway_url="http://gw:8080",
    api_key="svc-key",
    api_key_header="X-API-Key",
    concurrency=2,
    poll_seconds=0.05,
    reclaim_seconds=0.01,
    request_timeout=5.0,
)


def _line(cid, content="hi", url="/v1/chat/completions"):
    return json.dumps(
        {"custom_id": cid, "method": "POST", "url": url, "body": {"messages": [{"role": "user", "content": content}]}}
    )


def _setup(lines, endpoint="/v1/chat/completions", expires_in=3600, status="validating"):
    obj = MemoryObjectStore()
    store = MemoryBatchStore()
    tenant, file_id, batch_id = "tA", "file-in", "batch-1"
    blob = ("\n".join(lines) + "\n").encode()
    obj.put(f"{tenant}/{file_id}", blob)
    store.create_file(
        FileRecord(
            id=file_id,
            tenant=tenant,
            bytes=len(blob),
            created_at=1,
            filename="in.jsonl",
            purpose="batch",
            object_key=f"{tenant}/{file_id}",
            line_count=len(lines),
        )
    )
    now = int(time())
    store.create_batch(
        BatchRecord(
            id=batch_id,
            tenant=tenant,
            endpoint=endpoint,
            input_file_id=file_id,
            completion_window="24h",
            created_at=now,
            expires_at=now + expires_in,
            status=status,
            total=len(lines),
        )
    )
    store.enqueue(tenant, batch_id)
    return obj, store, tenant, batch_id


def _run(obj, store, tenant, batch_id, handler):
    async def _go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await process_batch(_CONFIG, obj, store, tenant, batch_id, client)

    asyncio.run(_go())


def test_process_all_success_writes_output_file():
    obj, store, tenant, batch_id = _setup([_line("a"), _line("b")])
    _run(
        obj,
        store,
        tenant,
        batch_id,
        lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}),
    )
    record = store.get_batch(tenant, batch_id)
    assert record.status == "completed"
    assert record.completed == 2 and record.failed == 0
    assert record.output_file_id and record.error_file_id is None
    output = obj.get(f"{tenant}/{record.output_file_id}").decode().strip().splitlines()
    assert len(output) == 2
    assert json.loads(output[0])["custom_id"] in {"a", "b"}


def test_process_mixed_success_and_error_splits_files():
    # The discriminator is in the content so the mock gateway can fail just the "bad" item.
    obj, store, tenant, batch_id = _setup([_line("ok", content="ok"), _line("bad", content="bad")])

    def handler(req):
        failing = json.loads(req.content)["messages"][0]["content"] == "bad"
        return httpx.Response(400 if failing else 200, json={"detail": "nope"} if failing else {"choices": []})

    _run(obj, store, tenant, batch_id, handler)
    record = store.get_batch(tenant, batch_id)
    assert record.status == "completed"
    assert record.completed == 1 and record.failed == 1
    assert record.output_file_id and record.error_file_id
    assert len(obj.get(f"{tenant}/{record.error_file_id}").decode().strip().splitlines()) == 1


def test_malformed_line_and_endpoint_mismatch_error_out():
    obj, store, tenant, batch_id = _setup(["{not json", _line("x", url="/v1/embeddings")])
    _run(obj, store, tenant, batch_id, lambda req: httpx.Response(200, json={}))
    record = store.get_batch(tenant, batch_id)
    assert record.completed == 0 and record.failed == 2
    assert record.error_file_id is not None


def test_replay_sends_tenant_and_service_key_headers():
    seen = {}
    obj, store, tenant, batch_id = _setup([_line("a")])

    def handler(req):
        seen["sandbox"] = req.headers.get("X-Sandbox-ID")
        seen["key"] = req.headers.get("X-API-Key")
        return httpx.Response(200, json={})

    _run(obj, store, tenant, batch_id, handler)
    assert seen["sandbox"] == "tA"  # the gateway enforces tenant binding on this
    assert seen["key"] == "svc-key"


def test_cancelling_batch_finalizes_cancelled():
    obj, store, tenant, batch_id = _setup([_line("a")], status="cancelling")
    _run(obj, store, tenant, batch_id, lambda req: httpx.Response(200, json={}))
    assert store.get_batch(tenant, batch_id).status == "cancelled"


def test_expired_batch_marked_expired():
    obj, store, tenant, batch_id = _setup([_line("a")], expires_in=-10)
    _run(obj, store, tenant, batch_id, lambda req: httpx.Response(200, json={}))
    assert store.get_batch(tenant, batch_id).status == "expired"


def test_missing_input_content_fails_batch():
    obj, store, tenant, batch_id = _setup([_line("a")])
    obj.delete(f"{tenant}/file-in")  # blob gone
    _run(obj, store, tenant, batch_id, lambda req: httpx.Response(200, json={}))
    record = store.get_batch(tenant, batch_id)
    assert record.status == "failed" and record.error


def test_process_is_idempotent_on_terminal_batch():
    obj, store, tenant, batch_id = _setup([_line("a")])
    _run(obj, store, tenant, batch_id, lambda req: httpx.Response(200, json={}))
    # A second run (e.g. a re-delivered claim) is a no-op because the batch is terminal.
    _run(obj, store, tenant, batch_id, lambda req: httpx.Response(500, json={}))
    assert store.get_batch(tenant, batch_id).status == "completed"


def test_run_once_claims_processes_and_acks():
    obj, store, tenant, batch_id = _setup([_line("a")])

    async def _go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={}))) as client:
            first = await run_once(_CONFIG, obj, store, client)
            second = await run_once(_CONFIG, obj, store, client)  # queue now empty + acked
        return first, second

    first, second = asyncio.run(_go())
    assert first is True and second is False
    assert store.get_batch(tenant, batch_id).status == "completed"


def test_run_worker_reclaims_and_stops():
    obj, store = MemoryObjectStore(), MemoryBatchStore()  # empty queue

    async def _go():
        stop = asyncio.Event()

        async def stopper():
            await asyncio.sleep(0.06)
            stop.set()

        await asyncio.gather(run_worker(_CONFIG, obj, store, stop), stopper())

    asyncio.run(_go())  # exercises the reclaim branch, the empty-queue poll, and the stop path


def test_amain_is_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("BATCH_API_ENABLED", raising=False)
    asyncio.run(_amain())  # batch disabled -> returns without building stores
