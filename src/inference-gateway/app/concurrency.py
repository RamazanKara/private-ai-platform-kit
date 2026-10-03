"""Bounded request concurrency with fast-fail load shedding.

A pure ASGI middleware rather than logic inside the ``request_context`` HTTP middleware:
``await self.app(...)`` returns only after the whole response body has been sent (for a
stream, after the last chunk) and raises if the request is cancelled, so the slot is
released in exactly one ``finally`` on every path. The earlier design released the slot
from a wrapper around the response body iterator; a client that disconnected before the
body started never ran that wrapper's ``finally``, leaking the slot until every request
was shed with a 503.
"""

from __future__ import annotations

from collections.abc import Callable

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.metrics import INFLIGHT
from app.request_context import _overloaded_response


class ConcurrencyLimitMiddleware:
    """Shed load with a 503 once ``limit`` governed requests are in flight.

    Installed inside ``request_context`` so it only counts requests that already passed
    authentication and rate limiting, and so the shed response reuses the request id and
    sandbox the outer middleware resolved. The counter lives on ``app.state.inflight``.
    The check and increment have no ``await`` between them, so they are atomic on the
    event loop.
    """

    def __init__(self, app: ASGIApp, *, limit: int, applies_to: Callable[[str], bool]) -> None:
        self.app = app
        self.limit = limit
        self.applies_to = applies_to

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.limit <= 0 or not self.applies_to(scope["path"]):
            await self.app(scope, receive, send)
            return
        state = scope["app"].state
        if state.inflight >= self.limit:
            response = _overloaded_response(Request(scope, receive))
            await response(scope, receive, send)
            return
        state.inflight += 1
        INFLIGHT.set(state.inflight)
        try:
            await self.app(scope, receive, send)
        finally:
            state.inflight -= 1
            INFLIGHT.set(state.inflight)
