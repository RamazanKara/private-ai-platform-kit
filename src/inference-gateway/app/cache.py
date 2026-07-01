"""Exact-match per-sandbox response cache for non-streaming chat completions.

Keyed by ``(sandbox_id, canonical-payload)`` so a repeated identical request returns the
prior runtime response without re-hitting the runtime, and so one tenant's cached answer
is never served to another. Entries expire after a TTL and the store is bounded with LRU
eviction. Streaming responses are never cached.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict
from time import time
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.settings import Settings

try:  # redis is an optional dependency; only present when the redis backend is used.
    from redis.exceptions import RedisError as _RedisError

    _CACHE_BACKEND_ERRORS: tuple[type[BaseException], ...] = (_RedisError, OSError)
except ImportError:  # pragma: no cover - redis always installed in the gateway image
    _CACHE_BACKEND_ERRORS = (OSError,)

_LOGGER = logging.getLogger("ai_platform_ops_lab.cache")


def cache_key(sandbox_id: str, payload: dict[str, Any]) -> str:
    """Return a stable cache key for a sandbox + resolved request payload.

    ``stream`` is excluded so it never participates in the key; everything else
    (model, messages, tools, sampling params) is canonicalized and hashed.
    """
    keyed = {k: v for k, v in payload.items() if k != "stream"}
    canonical = json.dumps(keyed, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{sandbox_id}\x00{canonical}".encode()).hexdigest()


class ResponseCacheBackend(Protocol):
    """Protocol for response caches that get/set runtime responses by cache_key()."""

    def get(self, key: str) -> dict[str, Any] | None: ...

    def set(self, key: str, value: dict[str, Any]) -> None: ...


class ResponseCache:
    """In-memory TTL + LRU cache of runtime responses keyed by cache_key().

    Process-local: each gateway replica keeps its own store, so under horizontal
    scale-out the effective hit rate degrades. Use the Redis backend
    (RESPONSE_CACHE_BACKEND=redis) for a cache shared across replicas.
    """

    def __init__(self, max_entries: int, ttl_seconds: int) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._store: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    def get(self, key: str) -> dict[str, Any] | None:
        """Return the cached response for the key, or None when absent/expired."""
        item = self._store.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at < time():
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value: dict[str, Any]) -> None:
        """Store a response under the key, evicting the oldest entry past the bound."""
        self._store[key] = (time() + self.ttl_seconds, value)
        self._store.move_to_end(key)
        while len(self._store) > self.max_entries:
            self._store.popitem(last=False)


class RedisResponseCache:
    """Response cache shared across gateway replicas via Redis string keys with TTL.

    Stores the JSON-serialized runtime response under a prefixed, sandbox-scoped key so
    every replica observes the same cache, keeping the hit rate stable under scale-out.
    """

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.ttl_seconds = settings.response_cache_ttl_seconds
        self.key_prefix = settings.response_cache_key_prefix
        if client is None:
            try:
                import redis
            except ImportError as exc:
                raise RuntimeError("redis package is required when RESPONSE_CACHE_BACKEND=redis") from exc
            client = redis.Redis.from_url(
                settings.response_cache_redis_url,
                decode_responses=True,
                socket_timeout=settings.response_cache_redis_timeout_seconds,
                socket_connect_timeout=settings.response_cache_redis_timeout_seconds,
            )
        self.client = client

    def _key(self, key: str) -> str:
        return f"{self.key_prefix}:{key}"

    def get(self, key: str) -> dict[str, Any] | None:
        """Return the cached response from Redis, or None when absent or unreadable.

        A cache is an optimization, never a dependency: a Redis outage degrades to a
        cache miss (the runtime is called) instead of failing the request.
        """
        try:
            raw = self.client.get(self._key(key))
        except _CACHE_BACKEND_ERRORS as exc:
            _LOGGER.debug("response cache get failed; treating as miss: %s", type(exc).__name__)
            return None
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return value if isinstance(value, dict) else None

    def set(self, key: str, value: dict[str, Any]) -> None:
        """Store the response in Redis under the key with the configured TTL.

        A write failure is swallowed (the response is simply not cached) so a Redis
        outage never fails a request that the runtime already answered.
        """
        try:
            self.client.set(self._key(key), json.dumps(value, separators=(",", ":")), ex=self.ttl_seconds)
        except _CACHE_BACKEND_ERRORS as exc:
            _LOGGER.debug("response cache set failed; response not cached: %s", type(exc).__name__)


def build_response_cache(settings: Settings) -> ResponseCacheBackend:
    """Return a Redis-backed or in-memory response cache per the configured backend."""
    if settings.response_cache_backend == "redis":
        return RedisResponseCache(settings)
    return ResponseCache(settings.response_cache_max_entries, settings.response_cache_ttl_seconds)
