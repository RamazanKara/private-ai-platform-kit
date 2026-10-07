"""Async HTTP client for Ollama/vLLM runtimes with retries and a circuit breaker."""

import random
import socket
from asyncio import sleep
from time import time
from typing import Any

import httpx

from app.cloud_providers import cloud_request, cloud_response, cloud_stream, credential_headers
from app.policy import CLOUD_BACKENDS, ModelRoute, ModelRoutingPolicy
from app.settings import Settings

REDACTED_MESSAGE_FIELDS = {"reasoning", "reasoning_content", "thinking"}

# Upstream HTTP statuses worth retrying: transient overload (429) and the gateway/
# server-error family a busy GPU runtime returns under queue pressure. A 4xx other
# than 429 is a client error and is never retried.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def is_runtime_fault(status_code: int) -> bool:
    """Whether an upstream status says the runtime is unhealthy, not the request.

    Only these count toward the circuit breaker. A 400/422 is one caller's malformed
    payload; counting it would let a single tenant open the circuit for everyone.
    """
    return status_code == 429 or status_code >= 500


def is_retryable_transport_error(exc: httpx.HTTPError) -> bool:
    """Whether a transport failure is safe to retry with the same generation request.

    A read timeout means the runtime accepted the request and is still generating;
    re-sending it multiplies GPU work and the caller's wait without improving the odds.
    """
    return isinstance(exc, httpx.TransportError) and not isinstance(exc, httpx.ReadTimeout)


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
        self.policy = ModelRoutingPolicy.default(settings)
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

    def _request_parts(
        self, payload: dict[str, Any], backend: str, endpoint: str, headers: dict[str, str] | None
    ) -> tuple[str, dict[str, Any], dict[str, str] | None, ModelRoute | None]:
        body = self._chat_completion_body(payload)
        if backend not in CLOUD_BACKENDS:
            return f"{self._base_url(backend)}/v1/{endpoint}", body, headers, None
        route = self.policy.resolve(body["model"], self.settings.model_id)
        if route.backend != backend:
            raise ValueError("provider route mismatch")
        if endpoint == "chat/completions" and not any(key in body for key in ("max_tokens", "max_completion_tokens")):
            body["max_completion_tokens"] = self.settings.max_completion_tokens
        url, body, auth = cloud_request(route, body, endpoint, self.settings.max_completion_tokens)
        return url, body, auth, route

    def _base_url(self, backend: str | None = None) -> str:
        resolved = backend or self.settings.runtime_backend
        if resolved == "vllm":
            return self.settings.vllm_base_url
        return self.settings.ollama_base_url

    def _chat_completions_url(self, backend: str | None = None) -> str:
        return f"{self._base_url(backend)}/v1/chat/completions"

    def _completions_url(self, backend: str | None = None) -> str:
        return f"{self._base_url(backend)}/v1/completions"

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

    @staticmethod
    def _retry_after_seconds(response: httpx.Response | None) -> float | None:
        """Return the upstream ``Retry-After`` delay in seconds, if a numeric one is set."""
        if response is None:
            return None
        raw = response.headers.get("retry-after")
        if not raw:
            return None
        try:
            value = float(raw.strip())
        except ValueError:
            return None
        return value if value >= 0 else None

    async def _sleep_before_retry(self, attempt: int, response: httpx.Response | None) -> None:
        """Sleep with exponential backoff + equal jitter, honoring ``Retry-After``.

        ``attempt`` is zero-based, so the base delay doubles each retry. Equal jitter
        (half fixed, half random) spreads retries so a fleet does not synchronize a
        thundering herd against a recovering runtime.
        """
        base = self.settings.runtime_retry_backoff_seconds * (2**attempt)
        retry_after = self._retry_after_seconds(response)
        if retry_after is not None:
            base = max(base, retry_after)
        delay = (base / 2.0) + random.random() * (base / 2.0)
        await sleep(delay)

    async def _post_json_with_retry(
        self,
        url: str,
        body: dict[str, Any],
        headers: dict[str, str] | None,
        resolved_backend: str,
    ) -> dict[str, Any]:
        """POST a JSON body with circuit, retry, and backoff; return the parsed object.

        Shared by the chat, embeddings, and any future non-streaming runtime calls so
        they get identical resilience behavior.
        """
        attempts = self.settings.runtime_max_retries + 1
        last_error: httpx.HTTPError | None = None
        data: Any = None
        client = self._client_instance()
        for attempt in range(attempts):
            self._check_circuit(resolved_backend)
            try:
                response = await client.post(url, json=body, headers=headers)
                # Retry transient upstream errors (5xx / 429) while attempts remain;
                # a non-retryable status falls through to raise_for_status below.
                if response.status_code in RETRYABLE_STATUS and attempt + 1 < attempts:
                    self._record_failure(resolved_backend)
                    await self._sleep_before_retry(attempt, response)
                    continue
                response.raise_for_status()
                data = response.json()
                self._record_success(resolved_backend)
                break
            except httpx.HTTPStatusError as exc:
                if is_runtime_fault(exc.response.status_code):
                    self._record_failure(resolved_backend)
                raise
            except httpx.HTTPError as exc:
                last_error = exc
                self._record_failure(resolved_backend)
                if not is_retryable_transport_error(exc) or attempt + 1 >= attempts:
                    raise
                await self._sleep_before_retry(attempt, None)
        else:
            raise last_error or RuntimeError("runtime request failed")
        if not isinstance(data, dict):
            raise ValueError("runtime returned a non-object JSON response")
        return data

    async def chat_completions(
        self,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        backend: str | None = None,
    ) -> dict[str, Any]:
        """Send a chat-completion request, retrying transient errors, and sanitize the result."""
        body = self._chat_completion_body(payload)
        resolved_backend = backend or self.settings.runtime_backend
        url, body, headers, route = self._request_parts(body, resolved_backend, "chat/completions", headers)
        data = await self._post_json_with_retry(
            url, body, headers, f"{resolved_backend}:{route.model_id}" if route else resolved_backend
        )
        if route:
            data = cloud_response(data, route)
        return sanitize_chat_completion(data)

    async def embeddings(
        self,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        backend: str | None = None,
    ) -> dict[str, Any]:
        """Send an embeddings request to the backend's OpenAI-compatible endpoint."""
        body = dict(payload)
        body["model"] = body.get("model") or self.settings.model_id
        resolved_backend = backend or self.settings.runtime_backend
        url, body, headers, route = self._request_parts(body, resolved_backend, "embeddings", headers)
        data = await self._post_json_with_retry(
            url, body, headers, f"{resolved_backend}:{route.model_id}" if route else resolved_backend
        )
        return cloud_response(data, route) if route else data

    async def completions(
        self,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        backend: str | None = None,
    ) -> dict[str, Any]:
        """Send a legacy text-completion request to the backend's ``/v1/completions`` endpoint.

        Mirrors :meth:`embeddings`: reuses the shared retry/circuit path and defaults the
        model. Legacy completions carry no assistant ``message`` object, so the chat
        reasoning sanitizer does not apply.
        """
        body = dict(payload)
        body["model"] = body.get("model") or self.settings.model_id
        resolved_backend = backend or self.settings.runtime_backend
        url, body, headers, route = self._request_parts(body, resolved_backend, "completions", headers)
        data = await self._post_json_with_retry(
            url, body, headers, f"{resolved_backend}:{route.model_id}" if route else resolved_backend
        )
        return cloud_response(data, route) if route else data

    async def stream_chat_completions(
        self,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        backend: str | None = None,
    ):
        """Yield raw streamed response chunks from the runtime chat-completions endpoint.

        A bounded retry runs only *before the first byte* is yielded. Once any chunk
        has reached the caller the response is committed: a mid-stream failure is
        re-raised, never retried, because a retry would append a second, different
        completion to the partial one the client already received.
        """
        body = self._chat_completion_body(payload)
        resolved_backend = backend or self.settings.runtime_backend
        url, body, headers, route = self._request_parts(body, resolved_backend, "chat/completions", headers)
        circuit = f"{resolved_backend}:{route.model_id}" if route else resolved_backend
        attempts = self.settings.runtime_max_retries + 1
        client = self._client_instance()
        last_error: httpx.HTTPError | None = None
        for attempt in range(attempts):
            self._check_circuit(circuit)
            streamed = False
            try:
                async with client.stream(
                    "POST",
                    url,
                    json=body,
                    headers=headers,
                ) as response:
                    if response.status_code in RETRYABLE_STATUS and attempt + 1 < attempts:
                        # Drain the body so the pooled connection is released, then retry.
                        await response.aread()
                        self._record_failure(circuit)
                        await self._sleep_before_retry(attempt, response)
                        continue
                    response.raise_for_status()
                    self._record_success(circuit)
                    chunks = cloud_stream(response, route) if route else response.aiter_bytes()
                    async for chunk in chunks:
                        streamed = True
                        yield chunk
                    return
            except httpx.HTTPStatusError as exc:
                if is_runtime_fault(exc.response.status_code):
                    self._record_failure(circuit)
                raise
            except httpx.HTTPError as exc:
                last_error = exc
                self._record_failure(circuit)
                if streamed or not is_retryable_transport_error(exc) or attempt + 1 >= attempts:
                    raise
                await self._sleep_before_retry(attempt, None)
        if last_error is not None:
            raise last_error

    async def health(self, backend: str | None = None) -> dict[str, Any]:
        """Probe the backend health endpoint and return its status payload."""
        resolved_backend = backend or self.settings.runtime_backend
        if resolved_backend in CLOUD_BACKENDS:
            for route in self.policy.routes:
                if route.backend == resolved_backend:
                    credential_headers(route)
            return {"status": "configured", "probe": "credentials_only"}
        # Short, fixed ceiling: this backs /readyz, whose kubelet probe times out after a
        # few seconds. A health endpoint that needs longer is itself the answer.
        timeout = httpx.Timeout(min(self.settings.request_timeout_seconds, 3.0))
        self._check_circuit(resolved_backend)
        client = self._client_instance()
        response = await client.get(f"{self._base_url(resolved_backend)}/healthz", timeout=timeout)
        if response.status_code == 404:
            response = await client.get(f"{self._base_url(resolved_backend)}/health", timeout=timeout)
        if response.status_code == 404:
            # Ollama exposes readiness at "/" (matching the ollama chart probe),
            # not /healthz or /health; fall back to it so /readyz can confirm the
            # backend is serving before reporting it unavailable.
            response = await client.get(f"{self._base_url(resolved_backend)}/", timeout=timeout)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            data = {"status": "ok"}
        if not isinstance(data, dict):
            data = {"status": "ok"}
        self._record_success(resolved_backend)
        return data
