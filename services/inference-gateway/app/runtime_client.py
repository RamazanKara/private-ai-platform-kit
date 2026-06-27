from typing import Any
from asyncio import sleep
from time import time

import httpx

from app.settings import Settings


REDACTED_MESSAGE_FIELDS = {"reasoning", "reasoning_content", "thinking"}


def sanitize_chat_completion(data: dict[str, Any]) -> dict[str, Any]:
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
                key: value
                for key, value in message.items()
                if key not in REDACTED_MESSAGE_FIELDS
            }
        sanitized_choices.append(sanitized_choice)
    sanitized["choices"] = sanitized_choices
    return sanitized


class RuntimeClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._failures: dict[str, int] = {}
        self._opened_until: dict[str, float] = {}

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
        body = self._chat_completion_body(payload)
        timeout = httpx.Timeout(self.settings.request_timeout_seconds)
        resolved_backend = backend or self.settings.runtime_backend
        attempts = self.settings.runtime_max_retries + 1
        last_error: httpx.HTTPError | None = None
        for attempt in range(attempts):
            self._check_circuit(resolved_backend)
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
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
        body = self._chat_completion_body(payload)
        timeout = httpx.Timeout(self.settings.request_timeout_seconds)
        resolved_backend = backend or self.settings.runtime_backend
        self._check_circuit(resolved_backend)
        async with httpx.AsyncClient(timeout=timeout) as client:
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
        resolved_backend = backend or self.settings.runtime_backend
        timeout = httpx.Timeout(min(self.settings.request_timeout_seconds, 10.0))
        self._check_circuit(resolved_backend)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{self._base_url(resolved_backend)}/healthz")
            if response.status_code == 404:
                response = await client.get(f"{self._base_url(resolved_backend)}/health")
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError:
                data = {"status": "ok"}
        if not isinstance(data, dict):
            data = {"status": "ok"}
        self._record_success(resolved_backend)
        return data
