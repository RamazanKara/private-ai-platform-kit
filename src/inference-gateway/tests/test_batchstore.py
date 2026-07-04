"""Tests for the file/batch metadata stores and work queue (ADR 0011)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.batchstore import (
    BATCH_CANCELLING,
    BATCH_COMPLETED,
    BATCH_VALIDATING,
    BatchRecord,
    FileRecord,
    MemoryBatchStore,
    RedisBatchStore,
    build_batch_store,
)


class FakeRedis:
    """Minimal in-memory Redis double supporting the commands RedisBatchStore uses."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    def set(self, key, value):
        self.kv[key] = value

    def get(self, key):
        return self.kv.get(key)

    def delete(self, *keys):
        for key in keys:
            self.kv.pop(key, None)
            self.lists.pop(key, None)
            self.hashes.pop(key, None)

    def exists(self, key):
        return 1 if key in self.kv or key in self.lists or key in self.hashes else 0

    def lpush(self, key, *values):
        lst = self.lists.setdefault(key, [])
        for value in values:
            lst.insert(0, value)
        return len(lst)

    def rpoplpush(self, src, dst):
        source = self.lists.get(src)
        if not source:
            return None
        value = source.pop()
        self.lists.setdefault(dst, []).insert(0, value)
        return value

    def lrange(self, key, start, end):
        lst = self.lists.get(key, [])
        return list(lst[start:]) if end == -1 else list(lst[start : end + 1])

    def lrem(self, key, count, value):
        lst = self.lists.get(key)
        if not lst:
            return 0
        removed = 0
        kept: list[str] = []
        for item in lst:
            if item == value and (count == 0 or removed < abs(count)):
                removed += 1
            else:
                kept.append(item)
        self.lists[key] = kept
        return removed

    def hset(self, key, field=None, value=None, mapping=None):
        table = self.hashes.setdefault(key, {})
        if mapping:
            table.update(mapping)
        if field is not None:
            table[field] = value
        return len(table)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hdel(self, key, *fields):
        table = self.hashes.get(key, {})
        return sum(1 for f in fields if table.pop(f, None) is not None)


@pytest.fixture(params=["memory", "redis"])
def store(request):
    if request.param == "memory":
        return MemoryBatchStore()
    return RedisBatchStore(SimpleNamespace(batch_key_prefix="t:batch"), client=FakeRedis())


def _file(fid="file-1", tenant="tA", purpose="batch"):
    return FileRecord(
        id=fid,
        tenant=tenant,
        bytes=10,
        created_at=1,
        filename="in.jsonl",
        purpose=purpose,
        object_key=f"{tenant}/{fid}",
        line_count=2,
    )


def _batch(bid="batch-1", tenant="tA", created_at=1):
    return BatchRecord(
        id=bid,
        tenant=tenant,
        endpoint="/v1/chat/completions",
        input_file_id="file-1",
        completion_window="24h",
        created_at=created_at,
        expires_at=created_at + 86400,
    )


def test_file_crud_and_tenant_isolation(store):
    store.create_file(_file())
    got = store.get_file("tA", "file-1")
    assert got is not None
    assert got.object_key == "tA/file-1"
    assert store.get_file("tB", "file-1") is None  # another tenant cannot read it
    assert [f.id for f in store.list_files("tA")] == ["file-1"]
    store.delete_file("tA", "file-1")
    assert store.get_file("tA", "file-1") is None


def test_batch_create_get_and_public_shape(store):
    store.create_batch(_batch())
    record = store.get_batch("tA", "batch-1")
    assert record is not None
    assert record.status == BATCH_VALIDATING
    public = record.to_public()
    assert public["object"] == "batch"
    assert public["request_counts"] == {"total": 0, "completed": 0, "failed": 0}
    assert public["endpoint"] == "/v1/chat/completions"


def test_update_batch_sets_fields_and_types(store):
    store.create_batch(_batch())
    updated = store.update_batch(
        "tA",
        "batch-1",
        {"status": BATCH_COMPLETED, "total": 3, "completed": 2, "failed": 1, "output_file_id": "file-out"},
    )
    assert updated is not None
    assert updated.status == BATCH_COMPLETED
    assert updated.total == 3 and updated.completed == 2 and updated.failed == 1
    assert updated.output_file_id == "file-out"
    # Re-read to confirm durability + typing (ints, not strings).
    reread = store.get_batch("tA", "batch-1")
    assert reread.total == 3
    assert reread.output_file_id == "file-out"


def test_update_batch_rejects_unknown_field(store):
    store.create_batch(_batch())
    with pytest.raises(ValueError):
        store.update_batch("tA", "batch-1", {"tenant": "evil"})


def test_update_absent_batch_returns_none(store):
    assert store.update_batch("tA", "missing", {"status": BATCH_COMPLETED}) is None


def test_cancel_transitions_and_terminal_is_unchanged(store):
    store.create_batch(_batch())
    cancelled = store.cancel_batch("tA", "batch-1")
    assert cancelled.status == BATCH_CANCELLING
    assert cancelled.cancelling_at is not None
    # A terminal batch is returned unchanged rather than re-cancelled.
    store.create_batch(_batch(bid="batch-2"))
    store.update_batch("tA", "batch-2", {"status": BATCH_COMPLETED})
    assert store.cancel_batch("tA", "batch-2").status == BATCH_COMPLETED
    assert store.cancel_batch("tA", "missing") is None


def test_list_batches_orders_desc_and_paginates(store):
    for i in range(1, 4):
        store.create_batch(_batch(bid=f"batch-{i}", created_at=i))
    ordered = store.list_batches("tA", limit=20)
    assert [b.id for b in ordered] == ["batch-3", "batch-2", "batch-1"]
    page = store.list_batches("tA", limit=1, after="batch-3")
    assert [b.id for b in page] == ["batch-2"]


def test_queue_fifo_claim_ack_and_reclaim(store):
    store.enqueue("tA", "batch-1")
    store.enqueue("tA", "batch-2")
    assert store.claim() == ("tA", "batch-1")  # FIFO
    assert store.claim() == ("tA", "batch-2")
    assert store.claim() is None  # nothing pending; both in flight
    # A stale in-flight item is re-queued and can be claimed again (crash recovery).
    assert store.reclaim(min_idle_seconds=0) == 2
    assert store.claim() in (("tA", "batch-1"), ("tA", "batch-2"))
    # Acking removes it from the in-flight set so reclaim won't resurface it.
    store.ack("tA", "batch-1")
    store.ack("tA", "batch-2")
    assert store.reclaim(min_idle_seconds=0) == 0


def test_reclaim_leaves_fresh_inflight_alone(store):
    store.enqueue("tA", "batch-1")
    store.claim()
    # A large idle threshold means the just-claimed item is not considered stale.
    assert store.reclaim(min_idle_seconds=3600) == 0


def test_build_batch_store_selects_backend():
    assert isinstance(build_batch_store(SimpleNamespace(batch_store_backend="memory")), MemoryBatchStore)
    redis_settings = SimpleNamespace(
        batch_store_backend="redis",
        batch_key_prefix="t:batch",
        batch_redis_url="redis://localhost:6379/2",
        batch_redis_timeout_seconds=0.5,
    )
    assert isinstance(build_batch_store(redis_settings), RedisBatchStore)
