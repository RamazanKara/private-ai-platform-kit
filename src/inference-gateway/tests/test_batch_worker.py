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


def test_non_object_json_line_fails_only_that_item():
    # `[1]` and `"x"` are valid JSON but not request objects; they must not fail the batch.
    obj, store, tenant, batch_id = _setup(["[1]", '"x"', _line("good")])
    _run(obj, store, tenant, batch_id, lambda req: httpx.Response(200, json={"choices": []}))
    record = store.get_batch(tenant, batch_id)
    assert record.status == "completed"
    assert record.completed == 1 and record.failed == 2


def test_long_batch_refreshes_its_claim_so_it_is_not_reclaimed():
    obj, store, tenant, batch_id = _setup([_line(str(i)) for i in range(6)])
    beats = []
    real_heartbeat = store.heartbeat

    def counting_heartbeat(claim):
        beats.append(claim.batch_id)
        return real_heartbeat(claim)

    store.heartbeat = counting_heartbeat

    async def _go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={}))) as client:
            return await run_once(_CONFIG, obj, store, client)

    assert asyncio.run(_go()) is True
    # One heartbeat per chunk of `concurrency` (2) lines.
    assert beats == [batch_id] * 3
    assert store.get_batch(tenant, batch_id).status == "completed"


def test_lost_claim_stops_without_finalizing_or_acking():
    # The worker stalled past the reclaim interval and the reaper re-queued its batch. It
    # must stop rather than race the next owner, and must not ack the re-queued message.
    obj, store, tenant, batch_id = _setup([_line(str(i)) for i in range(4)])
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 2:
            store.reclaim(0)
        return httpx.Response(200, json={})

    async def _go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await run_once(_CONFIG, obj, store, client)

    assert asyncio.run(_go()) is True
    assert store.get_batch(tenant, batch_id).status == "in_progress"  # left for the next owner
    assert len(calls) == 2  # the second chunk never ran here
    next_claim = store.claim()  # still queued for another worker
    assert (next_claim.tenant, next_claim.batch_id) == (tenant, batch_id)


def test_replay_names_the_batch_so_the_gateway_can_bind_the_tenant():
    seen = {}
    obj, store, tenant, batch_id = _setup([_line("a")])

    def handler(req):
        seen["batch"] = req.headers.get("X-Batch-ID")
        return httpx.Response(200, json={})

    _run(obj, store, tenant, batch_id, handler)
    assert seen["batch"] == batch_id


def _config(**overrides):
    values = {**_CONFIG.__dict__, **overrides}
    return WorkerConfig(**values)


def test_results_are_written_in_parts_and_assembled_in_order():
    obj, store, tenant, batch_id = _setup([_line(str(i), content=str(i)) for i in range(7)])

    async def _go():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json={"echo": json.loads(req.content)["messages"][0]["content"]})
            )
        ) as client:
            await process_batch(_config(part_lines=3), obj, store, tenant, batch_id, client)

    asyncio.run(_go())
    record = store.get_batch(tenant, batch_id)
    assert record.status == "completed" and record.completed == 7
    # Chunks of 2 against a part threshold of 3: a part of 4 items, then the final 3.
    assert record.output_parts == 2
    lines = obj.get(f"{tenant}/{record.output_file_id}").decode().strip().splitlines()
    assert [json.loads(line)["custom_id"] for line in lines] == [str(i) for i in range(7)]
    # Parts are removed once the result file exists.
    assert obj.list_keys(f"{tenant}/parts/") == []


def test_a_restarted_batch_resumes_after_its_last_checkpoint():
    # Simulates a crash after the first part: the record says 4 lines are done and one
    # output part exists. The resumed run must replay only the remaining lines.
    obj, store, tenant, batch_id = _setup([_line(str(i)) for i in range(6)])
    first = [json.dumps({"id": f"r{i}", "custom_id": str(i), "response": {}, "error": None}) for i in range(4)]
    obj.put(f"{tenant}/parts/{batch_id}/output-00000.jsonl", ("\n".join(first) + "\n").encode())
    store.update_batch(
        tenant, batch_id, {"status": "in_progress", "processed_lines": 4, "completed": 4, "output_parts": 1}
    )
    replayed = []

    def handler(req):
        replayed.append(req)
        return httpx.Response(200, json={})

    _run(obj, store, tenant, batch_id, handler)

    record = store.get_batch(tenant, batch_id)
    assert len(replayed) == 2
    assert record.status == "completed" and record.completed == 6
    lines = obj.get(f"{tenant}/{record.output_file_id}").decode().strip().splitlines()
    assert [json.loads(line)["custom_id"] for line in lines] == [str(i) for i in range(6)]


def test_expiry_during_processing_keeps_finished_results():
    obj, store, tenant, batch_id = _setup([_line(str(i)) for i in range(4)])
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 2:
            store.batches_expire_now = True
        return httpx.Response(200, json={})

    # Expire the batch after the first chunk by moving its deadline into the past.
    original = store.get_batch

    def get_batch(t, b):
        record = original(t, b)
        if record is not None and getattr(store, "batches_expire_now", False):
            record.expires_at = 0
        return record

    store.get_batch = get_batch
    _run(obj, store, tenant, batch_id, handler)
    record = original(tenant, batch_id)
    assert record.status == "expired"
    assert record.completed == 2  # the first chunk's results were kept
    assert record.output_file_id is not None


def test_worker_replays_through_a_real_gateway_with_a_scoped_key(tmp_path):
    # End to end over HTTP: an authenticated tenant uploads and submits a batch, and the
    # worker (holding only a batch_replay-scoped key) replays it through the gateway.
    import hashlib
    import socket
    import threading
    import time as _time

    import uvicorn
    from app.main import create_app
    from app.settings import Settings
    from fastapi.testclient import TestClient

    from tests.gateway_support import FakeRuntimeClient

    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    records = tmp_path / "records.json"
    records.write_text(
        json.dumps(
            {
                "records": [
                    {"sha256": digest("tenant-key"), "name": "tenant-key", "sandbox": "team-a"},
                    {"sha256": digest("worker-key"), "name": "worker", "scopes": ["batch_replay"]},
                ]
            }
        )
    )
    app = create_app(
        Settings(
            runtime_backend="ollama",
            ollama_base_url="http://ollama:11434",
            vllm_base_url="http://vllm:8000",
            model_id="m",
            request_timeout_seconds=5,
            api_key_auth_enabled=True,
            api_key_records_path=records,
            batch_api_enabled=True,
            batch_object_store_backend="memory",
            batch_store_backend="memory",
        )
    )
    app.state.runtime_client = FakeRuntimeClient(response={"id": "x", "object": "chat.completion", "choices": []})
    tenant = TestClient(app)
    upload = tenant.post(
        "/v1/files",
        headers={"X-API-Key": "tenant-key"},
        files={"file": ("in.jsonl", (_line("a") + "\n" + _line("b") + "\n").encode(), "application/jsonl")},
        data={"purpose": "batch"},
    )
    batch = tenant.post(
        "/v1/batches",
        headers={"X-API-Key": "tenant-key"},
        json={"input_file_id": upload.json()["id"], "endpoint": "/v1/chat/completions"},
    ).json()

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = _time.time() + 10
    while not server.started and _time.time() < deadline:
        _time.sleep(0.05)
    try:
        config = WorkerConfig(
            gateway_url=f"http://127.0.0.1:{port}",
            api_key="worker-key",
            api_key_header="X-API-Key",
            concurrency=2,
            poll_seconds=0.05,
            reclaim_seconds=60,
            request_timeout=5.0,
        )

        async def _go():
            async with httpx.AsyncClient() as client:
                return await run_once(config, app.state.object_store, app.state.batch_store, client)

        assert asyncio.run(_go()) is True
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    record = app.state.batch_store.get_batch("team-a", batch["id"])
    assert record.status == "completed"
    assert (record.completed, record.failed) == (2, 0)
