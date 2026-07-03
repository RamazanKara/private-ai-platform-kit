"""First-party Python client for the Private AI Platform Kit inference gateway.

A thin, dependency-light wrapper (httpx only) that sets the platform headers
(``X-Sandbox-ID``, optional bearer auth) and exposes the OpenAI-compatible endpoints, with
bounded retry/backoff on transient failures (honoring the gateway's ``Retry-After`` header
up to ``retry_after_cap`` seconds; a longer advertised delay fails fast with
:class:`GatewayRetryAfterError` since retrying sooner cannot succeed) and a streaming
chat helper. For full OpenAI
feature coverage use the ``openai`` SDK pointed at the gateway base URL (see
docs/client-examples.md); this client is the minimal first-party option for scripts and
services that do not want the larger dependency.

    from ai_platform_client import GatewayClient

    with GatewayClient("http://127.0.0.1:8080", api_key="...", sandbox_id="demo") as gw:
        reply = gw.chat([{"role": "user", "content": "hello"}])
        for chunk in gw.chat_stream([{"role": "user", "content": "stream please"}]):
            print(chunk, end="")
        vectors = gw.embeddings("embed this")
        usage = gw.usage()
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from types import TracebackType
from typing import Any

import httpx

# Retryable upstream statuses: 429 (rate limited / budget) and 5xx (transient server/runtime).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _parse_retry_after(value: str | None) -> int | None:
    """Parse a ``Retry-After`` header as non-negative integer seconds.

    The gateway only emits the delta-seconds form; malformed, negative, or absent
    values return ``None`` so the caller falls back to plain exponential backoff.
    """
    if value is None:
        return None
    try:
        seconds = int(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


class GatewayStreamError(RuntimeError):
    """Raised when the gateway emits a terminal error event mid-stream.

    The gateway signals an upstream failure after headers are sent with a final
    ``data: {"error": {...}}`` SSE event; surfacing it distinguishes a truncated
    stream from a completed one.
    """

    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(str(error.get("message") or "gateway stream failed"))
        self.error = error


class GatewayRetryAfterError(httpx.HTTPStatusError):
    """Raised without further retries when the gateway advertises a ``Retry-After`` longer than ``retry_after_cap``.

    Retrying sooner than the advertised delay cannot succeed (typically an exhausted
    sandbox budget window); ``retry_after`` tells the caller when to come back.
    """

    def __init__(self, message: str, *, request: httpx.Request, response: httpx.Response, retry_after: int) -> None:
        super().__init__(message, request=request, response=response)
        self.retry_after = retry_after


class GatewayClient:
    """Minimal client for the gateway's OpenAI-compatible API with retry and streaming."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        sandbox_id: str = "default",
        timeout: float = 120.0,
        max_retries: int = 2,
        retry_backoff: float = 0.25,
        retry_after_cap: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.sandbox_id = sandbox_id
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.retry_after_cap = retry_after_cap
        headers = {"X-Sandbox-ID": sandbox_id}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)

    def __enter__(self) -> GatewayClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def _sleep(self, duration: float) -> None:
        # Overridable/patchable for tests.
        time.sleep(duration)

    def _retry_delay(self, attempt: int, response: httpx.Response | None = None) -> float:
        """Return the delay before the next attempt.

        Exponential backoff (``retry_backoff * 2**attempt``), raised to the server's
        ``Retry-After`` when present. Headers beyond ``retry_after_cap`` never reach
        this method — :meth:`_request` fails fast with :class:`GatewayRetryAfterError`.
        """
        delay = self.retry_backoff * (2**attempt)
        retry_after = _parse_retry_after(response.headers.get("Retry-After")) if response is not None else None
        if retry_after is not None:
            delay = max(delay, min(float(retry_after), self.retry_after_cap))
        return delay

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Issue a request, retrying transient failures with exponential backoff.

        Retryable responses (429/5xx) that carry a ``Retry-After`` header wait for
        the longer of the backoff and the advertised delay. A ``Retry-After``
        strictly above ``retry_after_cap`` raises :class:`GatewayRetryAfterError`
        without any sleep, even on the first attempt: retrying sooner than the
        gateway's delay cannot succeed.
        """
        last_exc: httpx.HTTPError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    self._sleep(self._retry_delay(attempt))
                    continue
                raise
            if response.status_code in _RETRYABLE_STATUS:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                if retry_after is not None and retry_after > self.retry_after_cap:
                    raise GatewayRetryAfterError(
                        f"gateway advertised Retry-After {retry_after}s, beyond retry_after_cap"
                        f" ({self.retry_after_cap}s); not retrying",
                        request=response.request,
                        response=response,
                        retry_after=retry_after,
                    )
                if attempt < self.max_retries:
                    self._sleep(self._retry_delay(attempt, response))
                    continue
            response.raise_for_status()
            return response
        raise last_exc or RuntimeError("request failed with no response")

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, json=body).json()

    def chat(self, messages: list[dict[str, Any]], model: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Create a chat completion. Extra OpenAI params (tools, temperature, ...) pass through."""
        body: dict[str, Any] = {"messages": messages, **kwargs}
        if model is not None:
            body["model"] = model
        return self._post("/v1/chat/completions", body)

    def chat_stream(self, messages: list[dict[str, Any]], model: str | None = None, **kwargs: Any) -> Iterator[str]:
        """Stream a chat completion, yielding assistant content deltas as they arrive.

        Parses the OpenAI-compatible SSE stream and yields the text of each
        ``choices[0].delta.content`` chunk; terminal ``[DONE]`` and non-text events are
        skipped. A terminal gateway ``error`` event raises :class:`GatewayStreamError`
        so a truncated stream is never mistaken for a completed one. The streaming
        path is not retried once bytes are flowing.
        """
        body: dict[str, Any] = {"messages": messages, "stream": True, **kwargs}
        if model is not None:
            body["model"] = model
        with self._client.stream("POST", "/v1/chat/completions", json=body) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    parsed = json.loads(data)
                except ValueError:
                    continue
                if not isinstance(parsed, dict):
                    continue
                error = parsed.get("error")
                if isinstance(error, dict):
                    raise GatewayStreamError(error)
                choices = parsed.get("choices")
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield content

    def embeddings(self, text: str | list[str], model: str | None = None) -> dict[str, Any]:
        """Create embeddings for a string or list of strings."""
        body: dict[str, Any] = {"input": text}
        if model is not None:
            body["model"] = model
        return self._post("/v1/embeddings", body)

    def moderations(self, text: str | list[str]) -> dict[str, Any]:
        """Classify input against the gateway content policy."""
        return self._post("/v1/moderations", {"input": text})

    def batch(self, requests: list[dict[str, Any]]) -> dict[str, Any]:
        """Process a batch of chat-completion requests in one call (synchronous fan-out)."""
        return self._post("/v1/batches", {"requests": requests})

    def models(self) -> dict[str, Any]:
        """List the approved private models the gateway will route to."""
        return self._request("GET", "/v1/models").json()

    def usage(self) -> dict[str, Any]:
        """Return this sandbox's usage and estimated cost."""
        return self._request("GET", "/v1/usage").json()

    def sandbox_budget(self) -> dict[str, Any]:
        """Return this sandbox's budget usage, limits, and window TTL."""
        return self._request("GET", "/v1/sandbox/budget").json()
