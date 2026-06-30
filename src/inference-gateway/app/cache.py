"""Exact-match per-sandbox response cache for non-streaming chat completions.

Keyed by ``(sandbox_id, canonical-payload)`` so a repeated identical request returns the
prior runtime response without re-hitting the runtime, and so one tenant's cached answer
is never served to another. Entries expire after a TTL and the store is bounded with LRU
eviction. Streaming responses are never cached.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from time import time
from typing import Any


def cache_key(sandbox_id: str, payload: dict[str, Any]) -> str:
    """Return a stable cache key for a sandbox + resolved request payload.

    ``stream`` is excluded so it never participates in the key; everything else
    (model, messages, tools, sampling params) is canonicalized and hashed.
    """
    keyed = {k: v for k, v in payload.items() if k != "stream"}
    canonical = json.dumps(keyed, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{sandbox_id}\x00{canonical}".encode()).hexdigest()


class ResponseCache:
    """In-memory TTL + LRU cache of runtime responses keyed by cache_key()."""

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
