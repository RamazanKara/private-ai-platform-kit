"""Async HTTP client for Ollama/vLLM runtimes with retries and a circuit breaker."""

import socket
from asyncio import sleep
from time import time
from typing import Any

import httpx

from app.settings import Settings

REDACTED_MESSAGE_FIELDS = {"reasoning", "reasoning_content", "thinking"}

# Disable Nagle on the upstream sockets. The gateway proxies small JSON bodies over
# keep-alive connections; Nagle plus delayed ACKs can otherwise add a per-request
# stall on low-latency links. TCP_NODELAY keeps the upstream hop tight.
_SOCKET_OPTIONS = [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]


def sanitize_chat_completion(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the response with reasoning/thinking message fields removed."""
    sanitized = dict(data)
    choices = sanitized.get("choices")
    if not isinstance(choices, list):
        return sanitized
    sanitized_choices: list[Any] = []
    for choice in choices:
        if not isinstance(choice, dict):
            sanitized_choices.append(choice)
            continue
        sanitized_choice = dict(choice)
        message = sanitized_choice.get("message")
        if isinstance(message, dict):
            sanitized_choice["message"] = {
                key: value for key, value in message.items() if key not in REDACTED_MESSAGE_FIELDS
            }
        sanitized_choices.append(sanitized_choice)
    sanitized["choices"] = sanitized_choices
    return sanitized


class RuntimeClient:
    """Routes chat-completion calls to a runtime backend with resilience controls.

    A single :class:`httpx.AsyncClient` is created lazily and reused for the lifetime
    of the gateway process. Reusing the client keeps the connection pool warm and
    avoids reconstructing the client (and its TLS context) on every request, which
    otherwise dominates per-request cost on a busy gateway.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._failures: dict[str, int] = {}
        self._opened_until: dict[str, float] = {}
        self._client: httpx.AsyncClient | None = None

    def _client_instance(self) -> httpx.AsyncClient:
        """Return the shared async client, creating it on first use."""
        if self._client is None:
            limits = httpx.Limits(max_connections=256, max_keepalive_connections=128)
            transport = httpx.AsyncHTTPTransport(limits=limits, socket_options=_SOCKET_OPTIONS)
            self._client = httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(self.settings.request_timeout_seconds),
            )
        return self._client

    async def aclose(self) -> None:
        """Close the shared client; called on gateway shutdown."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _chat_completion_body(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = dict(payload)
        body["model"] = body.get("model") or self.settings.model_id
        return body

    def _base_url(self, backend: str | None = None) -> str:
        resolved = backend or self.settings.runtime_backend
        if resolved == "vllm":
            return self.settings.vllm_base_url
        return self.settings.ollama_base_url

    def _chat_completions_url(self, backend: str | None = None) -> str:
        return f"{self._base_url(backend)}/v1/chat/completions"

    def _check_circuit(self, backend: str) -> None:
        opened_until = self._opened_until.get(backend, 0)
        if opened_until > time():
            raise httpx.ConnectError(f"runtime circuit is open for backend {backend}")

    def _record_success(self, backend: str) -> None:
        self._failures[backend] = 0
        self._opened_until.pop(backend, None)

    def _record_failure(self, backend: str) -> None:
        threshold = self.settings.runtime_circuit_failure_threshold
        if threshold <= 0:
            return
        failures = self._failures.get(backend, 0) + 1
        self._failures[backend] = failures
        if failures >= threshold:
            self._opened_until[backend] = time() + self.settings.runtime_circuit_reset_seconds

    async def chat_completions(
        self,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        backend: str | None = None,
    ) -> dict[str, Any]:
        """Send a chat-completion request, retrying transient errors, and sanitize the result."""
        body = self._chat_completion_body(payload)
        resolved_backend = backend or self.settings.runtime_backend
        attempts = self.settings.runtime_max_retries + 1
        last_error: httpx.HTTPError | None = None
        client = self._client_instance()
        for attempt in range(attempts):
            self._check_circuit(resolved_backend)
            try:
                response = await client.post(
                    self._chat_completions_url(resolved_backend),
                    json=body,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
                self._record_success(resolved_backend)
                break
            except httpx.HTTPStatusError:
                self._record_failure(resolved_backend)
                raise
            except httpx.HTTPError as exc:
                last_error = exc
                self._record_failure(resolved_backend)
                if attempt + 1 >= attempts:
                    raise
                await sleep(self.settings.runtime_retry_backoff_seconds * (attempt + 1))
        else:
            raise last_error or RuntimeError("runtime request failed")
        if not isinstance(data, dict):
            raise ValueError("runtime returned a non-object JSON response")
        return sanitize_chat_completion(data)

    async def stream_chat_completions(
        self,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        backend: str | None = None,
    ):
        """Yield raw streamed response chunks from the runtime chat-completions endpoint."""
        body = self._chat_completion_body(payload)
        resolved_backend = backend or self.settings.runtime_backend
        self._check_circuit(resolved_backend)
        client = self._client_instance()
        async with client.stream(
            "POST",
            self._chat_completions_url(resolved_backend),
            json=body,
            headers=headers,
        ) as response:
            response.raise_for_status()
            self._record_success(resolved_backend)
            async for chunk in response.aiter_bytes():
                yield chunk

    async def health(self, backend: str | None = None) -> dict[str, Any]:
        """Probe the backend health endpoint and return its status payload."""
        resolved_backend = backend or self.settings.runtime_backend
        timeout = httpx.Timeout(min(self.settings.request_timeout_seconds, 10.0))
        self._check_circuit(resolved_backend)
        client = self._client_instance()
        response = await client.get(f"{self._base_url(resolved_backend)}/healthz", timeout=timeout)
        if response.status_code == 404:
            response = await client.get(f"{self._base_url(resolved_backend)}/health", timeout=timeout)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            data = {"status": "ok"}
        if not isinstance(data, dict):
            data = {"status": "ok"}
        self._record_success(resolved_backend)
        return data
