"""First-party Python client for the Private AI Platform Kit inference gateway.

A thin, dependency-light wrapper (httpx only) that sets the platform headers
(``X-Sandbox-ID``, optional bearer auth) and exposes the OpenAI-compatible endpoints.
For full OpenAI feature coverage use the ``openai`` SDK pointed at the gateway base URL
(see docs/client-examples.md); this client is the minimal first-party option for scripts
and services that do not want the larger dependency.

    from ai_platform_client import GatewayClient

    with GatewayClient("http://127.0.0.1:8080", api_key="...", sandbox_id="demo") as gw:
        reply = gw.chat([{"role": "user", "content": "hello"}])
        vectors = gw.embeddings("embed this")
        flags = gw.moderations("classify this")
        usage = gw.usage()
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx


class GatewayClient:
    """Minimal client for the gateway's OpenAI-compatible API."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        sandbox_id: str = "default",
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.sandbox_id = sandbox_id
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

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(path, json=body)
        response.raise_for_status()
        return response.json()

    def chat(self, messages: list[dict[str, Any]], model: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Create a chat completion. Extra OpenAI params (tools, temperature, ...) pass through."""
        body: dict[str, Any] = {"messages": messages, **kwargs}
        if model is not None:
            body["model"] = model
        return self._post("/v1/chat/completions", body)

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
        """Process a batch of chat-completion requests in one call."""
        return self._post("/v1/batches", {"requests": requests})

    def usage(self) -> dict[str, Any]:
        """Return this sandbox's usage and estimated cost."""
        response = self._client.get("/v1/usage")
        response.raise_for_status()
        return response.json()
