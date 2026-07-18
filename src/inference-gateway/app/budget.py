"""Per-sandbox request budget tracking with in-memory and Redis-backed enforcement."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil
from threading import Lock
from time import time
from typing import Any, Protocol

from app.admission import (
    AdmissionPolicyError,
    count_image_parts,
    max_requested_completion_tokens,
    message_prompt_chars,
    requested_completion_count,
)
from app.settings import Settings

try:  # redis is an optional dependency; only present when the redis backend is used.
    from redis.exceptions import RedisError as _RedisError

    _BUDGET_BACKEND_ERRORS: tuple[type[BaseException], ...] = (_RedisError, OSError)
except ImportError:  # pragma: no cover - redis always installed in the gateway image
    _BUDGET_BACKEND_ERRORS = (OSError,)


class BudgetBackendError(RuntimeError):
    """Raised when the budget backend (e.g. Redis) is unreachable, distinct from a limit hit.

    Lets the gateway return a 503 (retry later) when the shared budget store is down rather
    than surfacing the raw driver error as an unhandled 500.
    """


@dataclass
class BudgetUsage:
    """Accumulated sandbox usage counters within the current budget window."""

    requests: int = 0
    prompt_chars: int = 0
    estimated_tokens: int = 0


@dataclass(frozen=True)
class BudgetDelta:
    """The usage increment a single request would add to a sandbox budget."""

    requests: int
    prompt_chars: int
    estimated_tokens: int


@dataclass(frozen=True)
class BudgetReservation:
    """A successful budget reservation recording the request's cost and new usage."""

    sandbox_id: str
    request_count: int
    prompt_chars: int
    estimated_tokens: int
    usage: BudgetUsage
    backend: str

    def audit_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable summary of this reservation for audit logs."""
        return {
            "backend": self.backend,
            "request_count": self.request_count,
            "prompt_chars": self.prompt_chars,
            "estimated_tokens": self.estimated_tokens,
            "usage": asdict(self.usage),
        }


class SandboxBudgetTracker(Protocol):
    """Protocol for sandbox budget trackers that snapshot and reserve usage."""

    settings: Settings

    def snapshot(self, sandbox_id: str, settings: Settings | None = None) -> dict[str, Any]: ...

    def reserve(
        self,
        sandbox_id: str,
        payload: dict[str, Any],
        settings: Settings | None = None,
    ) -> BudgetReservation | None: ...


def budget_delta(settings: Settings, payload: dict[str, Any]) -> BudgetDelta:
    """Compute the request, prompt-char, and estimated-token cost of a payload.

    Counts prompt characters with the same ``extract_text_content`` the admission
    checks use, so multimodal content-part arrays and null content are charged by
    their text - not by the repr of their JSON scaffolding. Image parts add a flat
    ``image_part_token_estimate`` each (they carry no text but real runtime cost), the
    requested completion cap honors ``max_completion_tokens`` as well as the legacy
    ``max_tokens``, and the completion estimate is multiplied by ``n`` so a caller
    cannot request many completions - or dodge the cap by field name - for the price
    of one.
    """
    messages = payload.get("messages", [])
    prompt_chars = message_prompt_chars(messages)
    # Charge the larger of the two completion-cap fields (both are forwarded to the
    # runtime); a missing/invalid pair falls back to the cap, while an explicit 0 (the
    # embeddings path) is honored as zero completion cost.
    completion_tokens = max_requested_completion_tokens(payload)
    if completion_tokens is None:
        completion_tokens = settings.max_completion_tokens
    completions = requested_completion_count(payload)
    if not isinstance(completions, int) or isinstance(completions, bool) or completions <= 0:
        completions = 1
    image_tokens = count_image_parts(messages) * settings.image_part_token_estimate
    estimated_tokens = (
        ceil(prompt_chars / settings.budget_estimated_chars_per_token) + image_tokens + completion_tokens * completions
    )
    return BudgetDelta(
        requests=1,
        prompt_chars=prompt_chars,
        estimated_tokens=estimated_tokens,
    )


class InMemorySandboxBudgetTracker:
    """Process-local budget tracker using a lock and a sliding time window."""

    backend = "memory"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = Lock()
        self._usage: dict[str, BudgetUsage] = {}
        self._window_started_at: dict[str, float] = {}

    def _limits(self, settings: Settings | None = None) -> dict[str, int]:
        resolved = settings or self.settings
        return {
            "requests": resolved.sandbox_request_budget,
            "prompt_chars": resolved.sandbox_prompt_char_budget,
            "estimated_tokens": resolved.sandbox_estimated_token_budget,
        }

    def _current_usage(self, sandbox_id: str) -> BudgetUsage:
        now = time()
        started = self._window_started_at.get(sandbox_id)
        if started is None:
            self._window_started_at[sandbox_id] = now
        elif (
            self.settings.sandbox_budget_window_seconds > 0
            and now - started >= self.settings.sandbox_budget_window_seconds
        ):
            self._usage[sandbox_id] = BudgetUsage()
            self._window_started_at[sandbox_id] = now
        return self._usage.get(sandbox_id, BudgetUsage())

    def snapshot(self, sandbox_id: str, settings: Settings | None = None) -> dict[str, Any]:
        """Return the sandbox's current usage, limits, and window metadata."""
        resolved = settings or self.settings
        with self._lock:
            usage = self._current_usage(sandbox_id)
            window_started_at = self._window_started_at.get(sandbox_id)
        return {
            "enabled": resolved.sandbox_budget_enabled,
            "backend": self.backend,
            "sandbox_id": sandbox_id,
            "usage": asdict(usage),
            "limits": self._limits(resolved),
            "window_seconds": resolved.sandbox_budget_window_seconds,
            "window_started_at": window_started_at,
            "estimated_chars_per_token": resolved.budget_estimated_chars_per_token,
        }

    def reserve(
        self,
        sandbox_id: str,
        payload: dict[str, Any],
        settings: Settings | None = None,
    ) -> BudgetReservation | None:
        """Reserve budget for the payload, raising when a limit would be exceeded."""
        resolved = settings or self.settings
        if not resolved.sandbox_budget_enabled:
            return None

        delta = budget_delta(resolved, payload)
        with self._lock:
            current = self._current_usage(sandbox_id)
            proposed = BudgetUsage(
                requests=current.requests + delta.requests,
                prompt_chars=current.prompt_chars + delta.prompt_chars,
                estimated_tokens=current.estimated_tokens + delta.estimated_tokens,
            )
            check_budget_limits(resolved, proposed)
            self._usage[sandbox_id] = proposed

        return BudgetReservation(
            sandbox_id=sandbox_id,
            request_count=delta.requests,
            prompt_chars=delta.prompt_chars,
            estimated_tokens=delta.estimated_tokens,
            usage=proposed,
            backend=self.backend,
        )


REDIS_RESERVE_SCRIPT = """
local key = KEYS[1]
local ttl = tonumber(ARGV[1])
local add_requests = tonumber(ARGV[2])
local add_prompt_chars = tonumber(ARGV[3])
local add_estimated_tokens = tonumber(ARGV[4])
local limit_requests = tonumber(ARGV[5])
local limit_prompt_chars = tonumber(ARGV[6])
local limit_estimated_tokens = tonumber(ARGV[7])
local existing_ttl = redis.call('TTL', key)

local current_requests = tonumber(redis.call('HGET', key, 'requests') or '0')
local current_prompt_chars = tonumber(redis.call('HGET', key, 'prompt_chars') or '0')
local current_estimated_tokens = tonumber(redis.call('HGET', key, 'estimated_tokens') or '0')

local proposed_requests = current_requests + add_requests
local proposed_prompt_chars = current_prompt_chars + add_prompt_chars
local proposed_estimated_tokens = current_estimated_tokens + add_estimated_tokens

if limit_requests > 0 and proposed_requests > limit_requests then
  return {0, 'sandbox_request_budget_exceeded', 'request', proposed_requests, limit_requests}
end
if limit_prompt_chars > 0 and proposed_prompt_chars > limit_prompt_chars then
  return {0, 'sandbox_prompt_budget_exceeded', 'prompt character', proposed_prompt_chars, limit_prompt_chars}
end
if limit_estimated_tokens > 0 and proposed_estimated_tokens > limit_estimated_tokens then
  return {0, 'sandbox_token_budget_exceeded', 'estimated token', proposed_estimated_tokens, limit_estimated_tokens}
end

redis.call('HINCRBY', key, 'requests', add_requests)
redis.call('HINCRBY', key, 'prompt_chars', add_prompt_chars)
redis.call('HINCRBY', key, 'estimated_tokens', add_estimated_tokens)
-- Fixed window: arm expiry only for a new key or a key that somehow lost it.
-- Never refresh an existing positive TTL on each request (that would become a
-- sliding inactivity window and diverge from the in-memory implementation).
if ttl > 0 and existing_ttl < 0 then
  redis.call('EXPIRE', key, ttl)
end
return {1, proposed_requests, proposed_prompt_chars, proposed_estimated_tokens}
"""


class RedisSandboxBudgetTracker:
    """Distributed budget tracker enforcing limits atomically via a Redis Lua script."""

    backend = "redis"

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.settings = settings
        if client is None:
            try:
                import redis
            except ImportError as exc:
                raise RuntimeError("redis package is required when SANDBOX_BUDGET_BACKEND=redis") from exc
            client = redis.Redis.from_url(
                settings.sandbox_budget_redis_url,
                decode_responses=True,
                socket_timeout=settings.sandbox_budget_redis_timeout_seconds,
                socket_connect_timeout=settings.sandbox_budget_redis_timeout_seconds,
            )
        self.client = client

    def _limits(self, settings: Settings | None = None) -> dict[str, int]:
        resolved = settings or self.settings
        return {
            "requests": resolved.sandbox_request_budget,
            "prompt_chars": resolved.sandbox_prompt_char_budget,
            "estimated_tokens": resolved.sandbox_estimated_token_budget,
        }

    def _key(self, sandbox_id: str) -> str:
        return f"{self.settings.sandbox_budget_key_prefix}:{sandbox_id}"

    def snapshot(self, sandbox_id: str, settings: Settings | None = None) -> dict[str, Any]:
        """Return the sandbox's current usage, limits, and window TTL from Redis."""
        resolved = settings or self.settings
        try:
            raw = self.client.hgetall(self._key(sandbox_id)) or {}
            ttl = None
            if hasattr(self.client, "ttl"):
                ttl_value = self.client.ttl(self._key(sandbox_id))
                if isinstance(ttl_value, int) and ttl_value >= 0:
                    ttl = ttl_value
        except _BUDGET_BACKEND_ERRORS as exc:
            raise BudgetBackendError("sandbox budget backend is unavailable") from exc
        return {
            "enabled": resolved.sandbox_budget_enabled,
            "backend": self.backend,
            "sandbox_id": sandbox_id,
            "usage": {
                "requests": int(raw.get("requests", 0)),
                "prompt_chars": int(raw.get("prompt_chars", 0)),
                "estimated_tokens": int(raw.get("estimated_tokens", 0)),
            },
            "limits": self._limits(resolved),
            "window_seconds": resolved.sandbox_budget_window_seconds,
            "window_ttl_seconds": ttl,
            "estimated_chars_per_token": resolved.budget_estimated_chars_per_token,
        }

    def reserve(
        self,
        sandbox_id: str,
        payload: dict[str, Any],
        settings: Settings | None = None,
    ) -> BudgetReservation | None:
        """Reserve budget atomically in Redis, raising when a limit would be exceeded."""
        resolved = settings or self.settings
        if not resolved.sandbox_budget_enabled:
            return None

        delta = budget_delta(resolved, payload)
        try:
            result = self.client.eval(
                REDIS_RESERVE_SCRIPT,
                1,
                self._key(sandbox_id),
                resolved.sandbox_budget_window_seconds,
                delta.requests,
                delta.prompt_chars,
                delta.estimated_tokens,
                resolved.sandbox_request_budget,
                resolved.sandbox_prompt_char_budget,
                resolved.sandbox_estimated_token_budget,
            )
        except _BUDGET_BACKEND_ERRORS as exc:
            raise BudgetBackendError("sandbox budget backend is unavailable") from exc
        success = int(result[0])
        if not success:
            reason = str(result[1])
            label = str(result[2])
            value = int(result[3])
            limit = int(result[4])
            raise AdmissionPolicyError(
                reason,
                f"sandbox {label} budget would be {value}; limit is {limit}",
            )
        usage = BudgetUsage(
            requests=int(result[1]),
            prompt_chars=int(result[2]),
            estimated_tokens=int(result[3]),
        )
        return BudgetReservation(
            sandbox_id=sandbox_id,
            request_count=delta.requests,
            prompt_chars=delta.prompt_chars,
            estimated_tokens=delta.estimated_tokens,
            usage=usage,
            backend=self.backend,
        )


def check_budget_limits(settings: Settings, usage: BudgetUsage) -> None:
    """Raise AdmissionPolicyError if any usage counter exceeds its configured limit."""
    check_budget_limit(
        "sandbox_request_budget_exceeded",
        "request",
        usage.requests,
        settings.sandbox_request_budget,
    )
    check_budget_limit(
        "sandbox_prompt_budget_exceeded",
        "prompt character",
        usage.prompt_chars,
        settings.sandbox_prompt_char_budget,
    )
    check_budget_limit(
        "sandbox_token_budget_exceeded",
        "estimated token",
        usage.estimated_tokens,
        settings.sandbox_estimated_token_budget,
    )


def check_budget_limit(reason: str, label: str, value: int, limit: int) -> None:
    """Raise AdmissionPolicyError when a positive limit is exceeded by the value."""
    if limit > 0 and value > limit:
        raise AdmissionPolicyError(
            reason,
            f"sandbox {label} budget would be {value}; limit is {limit}",
        )


def build_sandbox_budget_tracker(settings: Settings) -> SandboxBudgetTracker:
    """Return a Redis or in-memory budget tracker per the configured backend."""
    if settings.sandbox_budget_backend == "redis":
        return RedisSandboxBudgetTracker(settings)
    return InMemorySandboxBudgetTracker(settings)
