"""Object storage for asynchronous-batch input/output/error blobs (ADR 0011).

The async batch subsystem stores JSONL files as objects: the caller-uploaded input file and
the generated output and error files. These blobs are large and multi-writer (the gateway
writes inputs, the batch-processor writes outputs), so they live in an object store rather
than in Redis or on the gateway's (deliberately stateless) local disk.

Unlike the response cache, an object store is a **source of truth**, not an optimization: a
read that cannot find its object raises ``ObjectNotFound`` and a backend error propagates,
rather than being swallowed. This module defines the ``ObjectStore`` protocol plus two
backends that need no external service — ``MemoryObjectStore`` (tests) and
``FilesystemObjectStore`` (single-node/local runs). The S3/MinIO backend used in cluster
deployments is added separately (it speaks the same protocol).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol, runtime_checkable


class ObjectNotFound(KeyError):
    """Raised by ``get`` when an object key is absent from the store."""


@runtime_checkable
class ObjectStore(Protocol):
    """Blob storage keyed by an opaque string key (e.g. ``<tenant>/<file_id>``).

    ``delete`` is idempotent (removing an absent key is not an error, matching S3), while
    ``get`` on an absent key raises ``ObjectNotFound`` so a missing input file is a clear
    failure rather than an empty read.
    """

    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...

    def list_keys(self, prefix: str = "") -> list[str]: ...


class MemoryObjectStore:
    """In-process dict-backed object store for tests and ephemeral local runs."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> None:
        _validate_key(key)
        self._store[key] = bytes(data)

    def get(self, key: str) -> bytes:
        try:
            return self._store[key]
        except KeyError as exc:
            raise ObjectNotFound(key) from exc

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self._store

    def list_keys(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self._store if k.startswith(prefix))


class FilesystemObjectStore:
    """Filesystem-backed object store rooted at a directory (single-node/local runs).

    Keys map to files under ``root``. Every key is validated to resolve *inside* the root so
    a crafted key (``..`` segments, an absolute path) cannot read or write outside the store.
    Writes are atomic (temp file + ``os.replace``) so a concurrent reader never sees a partial
    object.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        _validate_key(key)
        candidate = (self._root / key).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise ValueError(f"object key escapes store root: {key!r}")
        return candidate

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise ObjectNotFound(key) from exc

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_keys(self, prefix: str = "") -> list[str]:
        keys: list[str] = []
        for path in self._root.rglob("*"):
            if path.is_file() and not path.name.endswith(".tmp"):
                rel = path.relative_to(self._root).as_posix()
                if rel.startswith(prefix):
                    keys.append(rel)
        return sorted(keys)


def _validate_key(key: str) -> None:
    """Reject empty, whitespace-padded, absolute, or traversal keys before they touch a backend.

    Keys are internal (``<tenant>/<file_id>``), so this is defence-in-depth against a tenant id
    or file id that ever carries a separator or ``..``; it keeps the memory and filesystem
    backends behaving identically on bad input.
    """
    if not key or key != key.strip() or key.startswith("/") or "\\" in key:
        raise ValueError(f"invalid object key: {key!r}")
    if any(part in ("", ".", "..") for part in key.split("/")):
        raise ValueError(f"invalid object key: {key!r}")
