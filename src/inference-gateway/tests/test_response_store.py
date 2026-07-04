"""Tests for the stateful Responses store (ADR 0012)."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from app.response_store import (
    MemoryResponseStore,
    RedisResponseStore,
    StoredResponse,
    build_response_store,
)


class FakeRedis:
    """Minimal Redis double supporting the string ops RedisResponseStore uses."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def set(self, key, value, ex=None):
        self.kv[key] = value

    def get(self, key):
        return self.kv.get(key)

    def delete(self, *keys):
        return sum(1 for k in keys if self.kv.pop(k, None) is not None)


def _record(rid="resp-1", tenant="tA"):
    return StoredResponse(
        id=rid,
        tenant=tenant,
        created_at=1,
        model="m",
        body={"id": rid, "object": "response"},
        input_items=[{"role": "user", "content": "hi"}],
        messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}],
        previous_response_id=None,
    )


@pytest.fixture(params=["memory", "redis"])
def store(request):
    if request.param == "memory":
        return MemoryResponseStore(retention_seconds=3600)
    return RedisResponseStore(
        SimpleNamespace(responses_key_prefix="t:resp", responses_retention_seconds=3600), client=FakeRedis()
    )


def test_create_get_roundtrip_and_tenant_isolation(store):
    store.create(_record())
    got = store.get("tA", "resp-1")
    assert got is not None
    assert got.body["object"] == "response"
    assert got.messages[-1]["content"] == "yo"
    assert store.get("tB", "resp-1") is None  # another tenant cannot read it


def test_get_absent_returns_none(store):
    assert store.get("tA", "missing") is None


def test_delete_then_gone(store):
    store.create(_record())
    assert store.delete("tA", "resp-1") is True
    assert store.get("tA", "resp-1") is None
    assert store.delete("tA", "resp-1") is False  # already gone


def test_memory_ttl_expiry():
    store = MemoryResponseStore(retention_seconds=0)  # expires immediately
    store.create(_record())
    time.sleep(0.01)
    assert store.get("tA", "resp-1") is None


def test_build_response_store_selects_backend():
    memory = build_response_store(SimpleNamespace(responses_store_backend="memory", responses_retention_seconds=3600))
    assert isinstance(memory, MemoryResponseStore)
    redis = build_response_store(
        SimpleNamespace(
            responses_store_backend="redis",
            responses_key_prefix="t:resp",
            responses_redis_url="redis://localhost:6379/3",
            responses_redis_timeout_seconds=0.5,
            responses_retention_seconds=3600,
        )
    )
    assert isinstance(redis, RedisResponseStore)
