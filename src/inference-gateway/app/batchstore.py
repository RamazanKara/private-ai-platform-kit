"""File and batch metadata stores plus the durable work queue for the async batch API (ADR 0011).

The object store (``objectstore``) holds the JSONL *blobs*; this module holds the small,
structured *records* (one per uploaded file and one per batch job) and the queue the
``batch-processor`` drains. Two backends implement the same ``BatchStore`` protocol:

- ``MemoryBatchStore``: process-local, for tests and single-replica local runs.
- ``RedisBatchStore``: shared, durable state for cluster deployments. It uses only single
  Redis structures (JSON blobs for immutable metadata, a hash for mutable batch state, lists for
  indexes and the reliable work queue). Lua transactions couple state/queue transitions so a
  crash cannot strand a claimed job or publish a batch record without its queue message.

Batch cancellation is best-effort: the API flips ``status`` to ``cancelling`` and the worker
finalizes to ``cancelled`` at its next item boundary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from threading import Lock
from time import time
from typing import Any, Protocol

# Batch lifecycle states (OpenAI batch object semantics).
BATCH_VALIDATING = "validating"
BATCH_IN_PROGRESS = "in_progress"
BATCH_FINALIZING = "finalizing"
BATCH_COMPLETED = "completed"
BATCH_FAILED = "failed"
BATCH_EXPIRED = "expired"
BATCH_CANCELLING = "cancelling"
BATCH_CANCELLED = "cancelled"
# States from which a cancel request is honored (anything not yet terminal).
_CANCELLABLE = frozenset({BATCH_VALIDATING, BATCH_IN_PROGRESS, BATCH_FINALIZING})

try:  # redis is an optional dependency; only present when the redis backend is used.
    from redis.exceptions import RedisError as _RedisError

    _BATCH_BACKEND_ERRORS: tuple[type[BaseException], ...] = (_RedisError, OSError)
except ImportError:  # pragma: no cover - redis always installed in the gateway image
    _BATCH_BACKEND_ERRORS = (OSError,)


class BatchStoreError(RuntimeError):
    """Raised when the batch metadata backend (e.g. Redis) is unreachable."""


@dataclass
class FileRecord:
    """Metadata for an uploaded (or generated) JSONL file; the blob lives in the object store."""

    id: str
    tenant: str
    bytes: int
    created_at: int
    filename: str
    purpose: str  # batch | batch_output | batch_error
    object_key: str
    line_count: int = 0

    def to_public(self) -> dict[str, Any]:
        """Return the OpenAI file-object shape (internal tenant/object_key omitted)."""
        return {
            "id": self.id,
            "object": "file",
            "bytes": self.bytes,
            "created_at": self.created_at,
            "filename": self.filename,
            "purpose": self.purpose,
            "status": "processed",
        }


@dataclass
class BatchRecord:
    """A batch job: its immutable request plus mutable lifecycle state and counts."""

    id: str
    tenant: str
    endpoint: str
    input_file_id: str
    completion_window: str
    created_at: int
    expires_at: int
    status: str = BATCH_VALIDATING
    metadata: dict[str, str] = field(default_factory=dict)
    output_file_id: str | None = None
    error_file_id: str | None = None
    in_progress_at: int | None = None
    finalizing_at: int | None = None
    completed_at: int | None = None
    failed_at: int | None = None
    expired_at: int | None = None
    cancelling_at: int | None = None
    cancelled_at: int | None = None
    total: int = 0
    completed: int = 0
    failed: int = 0
    error: str | None = None

    def to_public(self) -> dict[str, Any]:
        """Return the OpenAI batch-object shape (internal tenant omitted)."""
        body: dict[str, Any] = {
            "id": self.id,
            "object": "batch",
            "endpoint": self.endpoint,
            "input_file_id": self.input_file_id,
            "completion_window": self.completion_window,
            "status": self.status,
            "output_file_id": self.output_file_id,
            "error_file_id": self.error_file_id,
            "created_at": self.created_at,
            "in_progress_at": self.in_progress_at,
            "expires_at": self.expires_at,
            "finalizing_at": self.finalizing_at,
            "completed_at": self.completed_at,
            "failed_at": self.failed_at,
            "expired_at": self.expired_at,
            "cancelling_at": self.cancelling_at,
            "cancelled_at": self.cancelled_at,
            "request_counts": {"total": self.total, "completed": self.completed, "failed": self.failed},
            "metadata": self.metadata or {},
        }
        if self.error:
            body["errors"] = {"object": "list", "data": [{"message": self.error}]}
        return body


# Fields the API and worker may partially update on a batch record.
_MUTABLE_BATCH_FIELDS = frozenset(
    {
        "status",
        "output_file_id",
        "error_file_id",
        "in_progress_at",
        "finalizing_at",
        "completed_at",
        "failed_at",
        "expired_at",
        "cancelling_at",
        "cancelled_at",
        "total",
        "completed",
        "failed",
        "error",
    }
)


class BatchStore(Protocol):
    """File/batch record storage plus the reliable work queue the batch-processor drains."""

    backend: str

    def create_file(self, record: FileRecord) -> None: ...

    def get_file(self, tenant: str, file_id: str) -> FileRecord | None: ...

    def list_files(self, tenant: str) -> list[FileRecord]: ...

    def delete_file(self, tenant: str, file_id: str) -> None: ...

    def create_batch(self, record: BatchRecord) -> None: ...

    def create_and_enqueue(self, record: BatchRecord) -> None: ...

    def get_batch(self, tenant: str, batch_id: str) -> BatchRecord | None: ...

    def update_batch(self, tenant: str, batch_id: str, updates: dict[str, Any]) -> BatchRecord | None: ...

    def cancel_batch(self, tenant: str, batch_id: str) -> BatchRecord | None: ...

    def list_batches(self, tenant: str, limit: int = 20, after: str | None = None) -> list[BatchRecord]: ...

    def enqueue(self, tenant: str, batch_id: str) -> None: ...

    def claim(self) -> tuple[str, str] | None: ...

    def ack(self, tenant: str, batch_id: str) -> None: ...

    def reclaim(self, min_idle_seconds: float) -> int: ...


def _validated_updates(updates: dict[str, Any]) -> dict[str, Any]:
    unknown = set(updates) - _MUTABLE_BATCH_FIELDS
    if unknown:
        raise ValueError(f"cannot update non-mutable batch fields: {sorted(unknown)}")
    return updates


class MemoryBatchStore:
    """Process-local file/batch store and queue guarded by a single lock."""

    backend = "memory"

    def __init__(self) -> None:
        self._lock = Lock()
        self._files: dict[str, FileRecord] = {}
        self._batches: dict[str, BatchRecord] = {}
        self._pending: list[tuple[str, str]] = []
        self._inflight: dict[tuple[str, str], float] = {}

    @staticmethod
    def _fkey(tenant: str, file_id: str) -> str:
        return f"{tenant}\x00{file_id}"

    def create_file(self, record: FileRecord) -> None:
        with self._lock:
            self._files[self._fkey(record.tenant, record.id)] = record

    def get_file(self, tenant: str, file_id: str) -> FileRecord | None:
        with self._lock:
            return self._files.get(self._fkey(tenant, file_id))

    def list_files(self, tenant: str) -> list[FileRecord]:
        with self._lock:
            files = [f for f in self._files.values() if f.tenant == tenant]
        return sorted(files, key=lambda f: f.created_at, reverse=True)

    def delete_file(self, tenant: str, file_id: str) -> None:
        with self._lock:
            self._files.pop(self._fkey(tenant, file_id), None)

    def create_batch(self, record: BatchRecord) -> None:
        with self._lock:
            self._batches[self._fkey(record.tenant, record.id)] = record

    def create_and_enqueue(self, record: BatchRecord) -> None:
        with self._lock:
            self._batches[self._fkey(record.tenant, record.id)] = record
            self._pending.append((record.tenant, record.id))

    def get_batch(self, tenant: str, batch_id: str) -> BatchRecord | None:
        with self._lock:
            return self._batches.get(self._fkey(tenant, batch_id))

    def update_batch(self, tenant: str, batch_id: str, updates: dict[str, Any]) -> BatchRecord | None:
        _validated_updates(updates)
        with self._lock:
            record = self._batches.get(self._fkey(tenant, batch_id))
            if record is None:
                return None
            for key, value in updates.items():
                setattr(record, key, value)
            return record

    def cancel_batch(self, tenant: str, batch_id: str) -> BatchRecord | None:
        with self._lock:
            record = self._batches.get(self._fkey(tenant, batch_id))
            if record is None:
                return None
            if record.status in _CANCELLABLE:
                record.status = BATCH_CANCELLING
                record.cancelling_at = int(time())
            return record

    def list_batches(self, tenant: str, limit: int = 20, after: str | None = None) -> list[BatchRecord]:
        with self._lock:
            batches = sorted(
                (b for b in self._batches.values() if b.tenant == tenant),
                key=lambda b: b.created_at,
                reverse=True,
            )
        return _paginate(batches, limit, after)

    def enqueue(self, tenant: str, batch_id: str) -> None:
        with self._lock:
            self._pending.append((tenant, batch_id))

    def claim(self) -> tuple[str, str] | None:
        with self._lock:
            if not self._pending:
                return None
            message = self._pending.pop(0)
            self._inflight[message] = time()
            return message

    def ack(self, tenant: str, batch_id: str) -> None:
        with self._lock:
            self._inflight.pop((tenant, batch_id), None)

    def reclaim(self, min_idle_seconds: float) -> int:
        cutoff = time() - min_idle_seconds
        requeued = 0
        with self._lock:
            for message, claimed_at in list(self._inflight.items()):
                if claimed_at <= cutoff:
                    del self._inflight[message]
                    self._pending.append(message)
                    requeued += 1
        return requeued


def _paginate(records: list[Any], limit: int, after: str | None) -> list[Any]:
    """Return up to ``limit`` records after the id ``after`` (cursor pagination)."""
    start = 0
    if after is not None:
        for index, record in enumerate(records):
            if record.id == after:
                start = index + 1
                break
    return records[start : start + max(1, limit)]


REDIS_CREATE_BATCH_SCRIPT = """
-- batch-create-v1
redis.call('SET', KEYS[1], ARGV[1])
redis.call('HSET', KEYS[2], unpack(ARGV, 3))
redis.call('LPUSH', KEYS[3], ARGV[2])
return 1
"""

REDIS_CREATE_AND_ENQUEUE_SCRIPT = """
-- batch-create-enqueue-v1
redis.call('SET', KEYS[1], ARGV[1])
redis.call('HSET', KEYS[2], unpack(ARGV, 4))
redis.call('LPUSH', KEYS[3], ARGV[2])
redis.call('LPUSH', KEYS[4], ARGV[3])
return 1
"""

REDIS_UPDATE_BATCH_SCRIPT = """
-- batch-update-v1
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
if #ARGV > 0 then redis.call('HSET', KEYS[1], unpack(ARGV)) end
return 1
"""

REDIS_CANCEL_BATCH_SCRIPT = """
-- batch-cancel-v1
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
local status = redis.call('HGET', KEYS[1], 'status')
if status == 'validating' or status == 'in_progress' or status == 'finalizing' then
  redis.call('HSET', KEYS[1], 'status', 'cancelling', 'cancelling_at', ARGV[1])
end
return 1
"""

REDIS_CLAIM_SCRIPT = """
-- batch-claim-v1
local message = redis.call('RPOPLPUSH', KEYS[1], KEYS[2])
if not message then return nil end
redis.call('HSET', KEYS[3], message, ARGV[1])
return message
"""

REDIS_ACK_SCRIPT = """
-- batch-ack-v1
redis.call('LREM', KEYS[1], 0, ARGV[1])
redis.call('HDEL', KEYS[2], ARGV[1])
return 1
"""

REDIS_RECLAIM_SCRIPT = """
-- batch-reclaim-v1
local claimed = redis.call('HGET', KEYS[2], ARGV[1])
if claimed and tonumber(claimed) > tonumber(ARGV[2]) then return 0 end
local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
redis.call('HDEL', KEYS[2], ARGV[1])
if removed > 0 then
  redis.call('LPUSH', KEYS[3], ARGV[1])
  return 1
end
return 0
"""


def _flatten_mapping(mapping: dict[str, str]) -> list[str]:
    """Flatten a mapping into Redis HSET field/value arguments."""
    return [item for pair in mapping.items() for item in pair]


class RedisBatchStore:
    """Shared, durable file/batch store and reliable work queue backed by Redis.

    Layout: a JSON blob per immutable file/batch record, a hash per
    batch for mutable lifecycle state and counts, per-tenant index lists, and a reliable queue
    (a ``pending`` list, a ``processing`` list, and a claim-time hash the reaper reads).
    """

    backend = "redis"

    def __init__(self, settings: Any, client: Any | None = None) -> None:
        self.prefix = settings.batch_key_prefix
        if client is None:
            try:
                import redis
            except ImportError as exc:  # pragma: no cover - redis always installed in the image
                raise RuntimeError("redis package is required when BATCH_STORE_BACKEND=redis") from exc
            client = redis.Redis.from_url(
                settings.batch_redis_url,
                decode_responses=True,
                socket_timeout=settings.batch_redis_timeout_seconds,
                socket_connect_timeout=settings.batch_redis_timeout_seconds,
            )
        self.client = client

    # --- key helpers ---
    def _file_key(self, tenant: str, file_id: str) -> str:
        return f"{self.prefix}:file:{tenant}:{file_id}"

    def _file_index(self, tenant: str) -> str:
        return f"{self.prefix}:files:{tenant}"

    def _batch_meta(self, tenant: str, batch_id: str) -> str:
        return f"{self.prefix}:batch:{tenant}:{batch_id}:meta"

    def _batch_state(self, tenant: str, batch_id: str) -> str:
        return f"{self.prefix}:batch:{tenant}:{batch_id}:state"

    def _batch_index(self, tenant: str) -> str:
        return f"{self.prefix}:batches:{tenant}"

    def _pending_key(self) -> str:
        return f"{self.prefix}:queue:pending"

    def _processing_key(self) -> str:
        return f"{self.prefix}:queue:processing"

    def _claims_key(self) -> str:
        return f"{self.prefix}:queue:claims"

    @staticmethod
    def _message(tenant: str, batch_id: str) -> str:
        return f"{tenant}\x00{batch_id}"

    @staticmethod
    def _split(message: str) -> tuple[str, str]:
        tenant, _, batch_id = message.partition("\x00")
        return tenant, batch_id

    # --- files ---
    def create_file(self, record: FileRecord) -> None:
        try:
            self.client.set(self._file_key(record.tenant, record.id), json.dumps(_file_to_dict(record)))
            self.client.lpush(self._file_index(record.tenant), record.id)
        except _BATCH_BACKEND_ERRORS as exc:
            raise BatchStoreError("batch metadata backend is unavailable") from exc

    def get_file(self, tenant: str, file_id: str) -> FileRecord | None:
        try:
            raw = self.client.get(self._file_key(tenant, file_id))
        except _BATCH_BACKEND_ERRORS as exc:
            raise BatchStoreError("batch metadata backend is unavailable") from exc
        return _file_from_json(raw)

    def list_files(self, tenant: str) -> list[FileRecord]:
        try:
            ids = self.client.lrange(self._file_index(tenant), 0, -1) or []
            records = [self.get_file(tenant, fid) for fid in ids]
        except _BATCH_BACKEND_ERRORS as exc:
            raise BatchStoreError("batch metadata backend is unavailable") from exc
        return sorted((r for r in records if r is not None), key=lambda f: f.created_at, reverse=True)

    def delete_file(self, tenant: str, file_id: str) -> None:
        try:
            self.client.delete(self._file_key(tenant, file_id))
            self.client.lrem(self._file_index(tenant), 0, file_id)
        except _BATCH_BACKEND_ERRORS as exc:
            raise BatchStoreError("batch metadata backend is unavailable") from exc

    # --- batches ---
    def create_batch(self, record: BatchRecord) -> None:
        state = _batch_state_map(record)
        flattened = _flatten_mapping(state)
        try:
            self.client.eval(
                REDIS_CREATE_BATCH_SCRIPT,
                3,
                self._batch_meta(record.tenant, record.id),
                self._batch_state(record.tenant, record.id),
                self._batch_index(record.tenant),
                json.dumps(_batch_meta_dict(record)),
                record.id,
                *flattened,
            )
        except _BATCH_BACKEND_ERRORS as exc:
            raise BatchStoreError("batch metadata backend is unavailable") from exc

    def create_and_enqueue(self, record: BatchRecord) -> None:
        state = _batch_state_map(record)
        flattened = _flatten_mapping(state)
        try:
            self.client.eval(
                REDIS_CREATE_AND_ENQUEUE_SCRIPT,
                4,
                self._batch_meta(record.tenant, record.id),
                self._batch_state(record.tenant, record.id),
                self._batch_index(record.tenant),
                self._pending_key(),
                json.dumps(_batch_meta_dict(record)),
                record.id,
                self._message(record.tenant, record.id),
                *flattened,
            )
        except _BATCH_BACKEND_ERRORS as exc:
            raise BatchStoreError("batch metadata backend is unavailable") from exc

    def get_batch(self, tenant: str, batch_id: str) -> BatchRecord | None:
        try:
            meta = self.client.get(self._batch_meta(tenant, batch_id))
            if not meta:
                return None
            state = self.client.hgetall(self._batch_state(tenant, batch_id)) or {}
        except _BATCH_BACKEND_ERRORS as exc:
            raise BatchStoreError("batch metadata backend is unavailable") from exc
        return _batch_from_parts(json.loads(meta), state)

    def update_batch(self, tenant: str, batch_id: str, updates: dict[str, Any]) -> BatchRecord | None:
        _validated_updates(updates)
        state_key = self._batch_state(tenant, batch_id)
        try:
            mapping = {k: _encode_state(v) for k, v in updates.items()}
            updated = self.client.eval(REDIS_UPDATE_BATCH_SCRIPT, 1, state_key, *_flatten_mapping(mapping))
            if not updated:
                return None
        except _BATCH_BACKEND_ERRORS as exc:
            raise BatchStoreError("batch metadata backend is unavailable") from exc
        return self.get_batch(tenant, batch_id)

    def cancel_batch(self, tenant: str, batch_id: str) -> BatchRecord | None:
        state_key = self._batch_state(tenant, batch_id)
        try:
            exists = self.client.eval(REDIS_CANCEL_BATCH_SCRIPT, 1, state_key, str(int(time())))
        except _BATCH_BACKEND_ERRORS as exc:
            raise BatchStoreError("batch metadata backend is unavailable") from exc
        return self.get_batch(tenant, batch_id) if exists else None

    def list_batches(self, tenant: str, limit: int = 20, after: str | None = None) -> list[BatchRecord]:
        try:
            ids = self.client.lrange(self._batch_index(tenant), 0, -1) or []
            records = [self.get_batch(tenant, bid) for bid in ids]
        except _BATCH_BACKEND_ERRORS as exc:
            raise BatchStoreError("batch metadata backend is unavailable") from exc
        ordered = sorted((r for r in records if r is not None), key=lambda b: b.created_at, reverse=True)
        return _paginate(ordered, limit, after)

    # --- queue ---
    def enqueue(self, tenant: str, batch_id: str) -> None:
        try:
            self.client.lpush(self._pending_key(), self._message(tenant, batch_id))
        except _BATCH_BACKEND_ERRORS as exc:
            raise BatchStoreError("batch metadata backend is unavailable") from exc

    def claim(self) -> tuple[str, str] | None:
        try:
            message = self.client.eval(
                REDIS_CLAIM_SCRIPT,
                3,
                self._pending_key(),
                self._processing_key(),
                self._claims_key(),
                str(time()),
            )
            if message is None:
                return None
        except _BATCH_BACKEND_ERRORS as exc:
            raise BatchStoreError("batch metadata backend is unavailable") from exc
        return self._split(message)

    def ack(self, tenant: str, batch_id: str) -> None:
        message = self._message(tenant, batch_id)
        try:
            self.client.eval(REDIS_ACK_SCRIPT, 2, self._processing_key(), self._claims_key(), message)
        except _BATCH_BACKEND_ERRORS as exc:
            raise BatchStoreError("batch metadata backend is unavailable") from exc

    def reclaim(self, min_idle_seconds: float) -> int:
        cutoff = time() - min_idle_seconds
        requeued = 0
        try:
            for message in self.client.lrange(self._processing_key(), 0, -1) or []:
                requeued += int(
                    self.client.eval(
                        REDIS_RECLAIM_SCRIPT,
                        3,
                        self._processing_key(),
                        self._claims_key(),
                        self._pending_key(),
                        message,
                        str(cutoff),
                    )
                )
        except _BATCH_BACKEND_ERRORS as exc:
            raise BatchStoreError("batch metadata backend is unavailable") from exc
        return requeued


# --- (de)serialization helpers for the Redis backend ---
_STATE_INT_FIELDS = ("total", "completed", "failed")
_STATE_OPTIONAL_INT_FIELDS = (
    "in_progress_at",
    "finalizing_at",
    "completed_at",
    "failed_at",
    "expired_at",
    "cancelling_at",
    "cancelled_at",
)
_STATE_OPTIONAL_STR_FIELDS = ("output_file_id", "error_file_id", "error")


def _file_to_dict(record: FileRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "tenant": record.tenant,
        "bytes": record.bytes,
        "created_at": record.created_at,
        "filename": record.filename,
        "purpose": record.purpose,
        "object_key": record.object_key,
        "line_count": record.line_count,
    }


def _file_from_json(raw: str | None) -> FileRecord | None:
    if not raw:
        return None
    data = json.loads(raw)
    return FileRecord(**data)


def _batch_meta_dict(record: BatchRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "tenant": record.tenant,
        "endpoint": record.endpoint,
        "input_file_id": record.input_file_id,
        "completion_window": record.completion_window,
        "created_at": record.created_at,
        "expires_at": record.expires_at,
        "metadata": record.metadata,
    }


def _batch_state_map(record: BatchRecord) -> dict[str, str]:
    state = {"status": record.status}
    for name in _STATE_INT_FIELDS:
        state[name] = str(getattr(record, name))
    for name in _STATE_OPTIONAL_INT_FIELDS + _STATE_OPTIONAL_STR_FIELDS:
        value = getattr(record, name)
        if value is not None:
            state[name] = _encode_state(value)
    return state


def _encode_state(value: Any) -> str:
    return "" if value is None else str(value)


def _batch_from_parts(meta: dict[str, Any], state: dict[str, str]) -> BatchRecord:
    record = BatchRecord(
        id=meta["id"],
        tenant=meta["tenant"],
        endpoint=meta["endpoint"],
        input_file_id=meta["input_file_id"],
        completion_window=meta["completion_window"],
        created_at=meta["created_at"],
        expires_at=meta["expires_at"],
        metadata=meta.get("metadata") or {},
        status=state.get("status", BATCH_VALIDATING),
    )
    for name in _STATE_INT_FIELDS:
        setattr(record, name, int(state.get(name, 0) or 0))
    for name in _STATE_OPTIONAL_INT_FIELDS:
        value = state.get(name)
        setattr(record, name, int(value) if value else None)
    for name in _STATE_OPTIONAL_STR_FIELDS:
        value = state.get(name)
        setattr(record, name, value if value not in (None, "") else None)
    return record


def build_batch_store(settings: Any) -> BatchStore:
    """Return a Redis-backed or in-memory batch store per the configured backend."""
    if settings.batch_store_backend == "redis":
        return RedisBatchStore(settings)
    return MemoryBatchStore()
