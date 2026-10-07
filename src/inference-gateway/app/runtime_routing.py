"""Runtime failover and shadow-routing helpers."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import Request

from app.governance import effective_settings, governed, reserve_budget, route_settings
from app.metrics import RUNTIME_FALLBACKS, SHADOW_REQUESTS
from app.request_context import _runtime_headers
from app.runtime_client import RuntimeClient
from app.settings import AdmissionPolicyError


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
            shadow_request = Request({**request.scope, "state": dict(request.scope["state"])})
            shadow_request.state.selected_route = shadow_route
            shadow_request.state.routing_role = "shadow"
            shadow_request.state.routing_attempts = []
            shadow_request.state.budget_settled = False
            shadow_request.state.cache_status = None
            settings = request.app.state.settings
            async with governed(shadow_request, settings, route=request.url.path, payload=shadow_payload) as call:
                effective = route_settings(
                    effective_settings(shadow_request, request.app.state.sandbox_policy_set, settings), shadow_route
                )
                effective.validate_admission(shadow_payload)
                await reserve_budget(shadow_request, effective, shadow_payload)
                call.runtime_response = await client.chat_completions(
                    shadow_payload, headers=headers, backend=shadow_route.backend
                )
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


def _start_attempt(request: Request, candidate: Any, payload: dict[str, Any]) -> dict[str, Any]:
    request.state.selected_route = candidate
    payload["model"] = candidate.model_id
    attempts = getattr(request.state, "routing_attempts", None)
    if attempts is None:
        attempts = []
        request.state.routing_attempts = attempts
    attempt = {"provider": candidate.backend, "model": candidate.model_id}
    attempts.append(attempt)
    return attempt


async def _chat_with_fallback(
    client: RuntimeClient, chain: list[Any], payload: dict[str, Any], request: Request
) -> dict[str, Any]:
    for index, candidate in enumerate(chain):
        attempt = _start_attempt(request, candidate, payload)
        try:
            result = await client.chat_completions(
                dict(payload), headers=_runtime_headers(request), backend=candidate.backend
            )
            attempt["status"] = "served"
            return result
        except httpx.HTTPError as exc:
            attempt["status"] = "failed"
            if _is_failover_worthy(exc) and index + 1 < len(chain):
                RUNTIME_FALLBACKS.labels(candidate.backend, chain[index + 1].backend).inc()
                continue
            _refuse_cloud_fallback(request, exc)
            raise
    raise RuntimeError("no runtime route available")


def _refuse_cloud_fallback(request: Request, exc: httpx.HTTPError) -> None:
    if _is_failover_worthy(exc) and getattr(request.state, "classification_blocked_routes", []):
        raise AdmissionPolicyError(
            "data_classification_denied", "local models are unavailable; confidential data cannot fall back to cloud"
        ) from exc


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
        receipt = _start_attempt(request, candidate, payload_dict)
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
            receipt["status"] = "served"
            return candidate_stream, candidate.backend, candidate.model_id, None
        except httpx.HTTPError as exc:
            receipt["status"] = "failed"
            await candidate_stream.aclose()
            last_exc = exc
            if _is_failover_worthy(exc) and index + 1 < len(chain):
                RUNTIME_FALLBACKS.labels(candidate.backend, chain[index + 1].backend).inc()
                continue
            _refuse_cloud_fallback(request, exc)
            raise
        receipt["status"] = "served"
        return candidate_stream, candidate.backend, candidate.model_id, first_chunk
    raise last_exc or RuntimeError("no runtime route available")
