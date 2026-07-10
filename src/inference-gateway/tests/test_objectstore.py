"""Tests for the batch object-store backends (ADR 0011)."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from app.objectstore import (
    FilesystemObjectStore,
    MemoryObjectStore,
    ObjectNotFound,
    ObjectStore,
    build_object_store,
)


@pytest.fixture(params=["memory", "filesystem"])
def store(request, tmp_path) -> ObjectStore:
    if request.param == "memory":
        return MemoryObjectStore()
    return FilesystemObjectStore(tmp_path / "objects")


def test_put_get_round_trips(store):
    store.put("tenant-a/file-1", b"hello\nworld\n")
    assert store.get("tenant-a/file-1") == b"hello\nworld\n"


def test_put_stream_round_trips_without_bytes_materialization(store):
    data = b"line one\nline two\n"
    store.put_stream("tenant-a/stream", BytesIO(data), len(data))
    assert store.get("tenant-a/stream") == data


def test_put_stream_rejects_size_mismatch(store):
    with pytest.raises(OSError):
        store.put_stream("tenant-a/stream", BytesIO(b"short"), 99)


def test_put_overwrites(store):
    store.put("k", b"one")
    store.put("k", b"two")
    assert store.get("k") == b"two"


def test_get_absent_raises_object_not_found(store):
    with pytest.raises(ObjectNotFound):
        store.get("missing")


def test_exists_reflects_presence(store):
    assert store.exists("k") is False
    store.put("k", b"x")
    assert store.exists("k") is True


def test_delete_is_idempotent(store):
    store.put("k", b"x")
    store.delete("k")
    assert store.exists("k") is False
    store.delete("k")  # deleting an absent key is not an error (matches S3 DELETE)


def test_list_keys_prefix_and_sorted(store):
    store.put("t1/b", b"1")
    store.put("t1/a", b"1")
    store.put("t2/c", b"1")
    assert store.list_keys("t1/") == ["t1/a", "t1/b"]
    assert store.list_keys() == ["t1/a", "t1/b", "t2/c"]


def test_satisfies_object_store_protocol(store):
    assert isinstance(store, ObjectStore)


@pytest.mark.parametrize("bad", ["", "   ", " k", "/abs", "a/../b", "..", "a//b", "a\\b"])
def test_put_rejects_unsafe_keys(store, bad):
    with pytest.raises(ValueError):
        store.put(bad, b"x")


def test_filesystem_persists_across_instances(tmp_path):
    root = tmp_path / "obj"
    FilesystemObjectStore(root).put("t/f", b"durable")
    assert FilesystemObjectStore(root).get("t/f") == b"durable"


def test_filesystem_traversal_escape_rejected(tmp_path):
    store = FilesystemObjectStore(tmp_path / "obj")
    with pytest.raises(ValueError):
        store.put("../escape", b"x")


def test_build_object_store_selects_backend(tmp_path):
    from app.objectstore_s3 import S3ObjectStore

    assert isinstance(build_object_store(SimpleNamespace(batch_object_store_backend="memory")), MemoryObjectStore)
    fs = build_object_store(
        SimpleNamespace(batch_object_store_backend="filesystem", batch_object_store_root=str(tmp_path / "o"))
    )
    assert isinstance(fs, FilesystemObjectStore)
    s3 = build_object_store(
        SimpleNamespace(
            batch_object_store_backend="s3",
            batch_s3_endpoint_url="http://minio:9000",
            batch_s3_bucket="b",
            batch_s3_region="us-east-1",
            batch_s3_access_key_id="",
            batch_s3_secret_access_key="",
        )
    )
    assert isinstance(s3, S3ObjectStore)
    with pytest.raises(ValueError):
        build_object_store(SimpleNamespace(batch_object_store_backend="bogus"))


def test_filesystem_list_ignores_partial_tmp(tmp_path):
    root = tmp_path / "obj"
    store = FilesystemObjectStore(root)
    store.put("t/f", b"x")
    # A stray temp file (an interrupted write) must not surface as a key.
    (root / "t" / "f.999.tmp").write_bytes(b"partial")
    assert store.list_keys() == ["t/f"]
