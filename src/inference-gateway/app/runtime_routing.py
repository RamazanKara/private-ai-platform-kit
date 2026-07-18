"""Runtime failover and shadow-routing helpers."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import Request

from app.metrics import RUNTIME_FALLBACKS, SHADOW_REQUESTS
from app.request_context import _runtime_headers
from app.runtime_client import RuntimeClient


def _schedule_shadow(client: RuntimeClient, shadow_route: Any, payload_dict: dict[str, Any], request: Request) -> None:
    """Fire a mirrored request to the shadow model, discarding its response and errors.

    Runs as a detached task so it never adds latency to or fails the caller's request;
    used to evaluate a candidate model on real traffic before promotion.
    """
    shadow_payload = dict(payload_dict)
    shadow_payload["model"] = shadow_route.model_id
    shadow_payload.pop("stream", None)
    headers = _runtime_headers(request)

    async def _run() -> None:
        try:
            await client.chat_completions(shadow_payload, headers=headers, backend=shadow_route.backend)
            SHADOW_REQUESTS.labels(shadow_route.backend, "ok").inc()
        except Exception:
            SHADOW_REQUESTS.labels(shadow_route.backend, "error").inc()

    # Hold a strong reference until completion so the detached task is not GC'd mid-flight.
    tasks: set[asyncio.Task[None]] = request.app.state.background_tasks
    task = asyncio.ensure_future(_run())
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def _is_failover_worthy(exc: Exception) -> bool:
    """Return whether an upstream failure should trigger a fallback to the next route.

    Connection/transport errors and an open circuit always fail over; an HTTP status
    error fails over only for retryable server-side statuses (5xx/429), never a client
    error like 400/404 that the next runtime would also reject.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, httpx.HTTPError)


async def _open_stream_with_fallback(
    client: RuntimeClient,
    chain: list[Any],
    payload_dict: dict[str, Any],
    request: Request,
) -> tuple[Any, str, str, bytes | None]:
    """Open a chat stream, failing over to the next route on a pre-first-byte error.

    Returns the live stream generator, the backend and model id that served it, and the
    primed first chunk (``None`` for an empty stream). Once the first chunk is returned
    the response is committed; later failures are handled by the stream body itself.
    """
    last_exc: httpx.HTTPError | None = None
    for index, candidate in enumerate(chain):
        attempt = dict(payload_dict)
        attempt["model"] = candidate.model_id
        candidate_stream = client.stream_chat_completions(
            attempt,
            headers=_runtime_headers(request),
            backend=candidate.backend,
        )
        try:
            first_chunk = await candidate_stream.__anext__()
        except StopAsyncIteration:
            return candidate_stream, candidate.backend, candidate.model_id, None
        except httpx.HTTPError as exc:
            await candidate_stream.aclose()
            last_exc = exc
            if _is_failover_worthy(exc) and index + 1 < len(chain):
                RUNTIME_FALLBACKS.labels(candidate.backend, chain[index + 1].backend).inc()
                continue
            raise
        return candidate_stream, candidate.backend, candidate.model_id, first_chunk
    raise last_exc or RuntimeError("no runtime route available")
