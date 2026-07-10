"""Bounded ASGI request-body buffering with deterministic 413 responses."""

from __future__ import annotations

from collections.abc import Mapping

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    """Reject declared or streamed request bodies that exceed a route limit."""

    def __init__(self, app: ASGIApp, max_bytes: int, path_limits: Mapping[str, int] | None = None) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.path_limits = dict(path_limits or {})

    async def _reject(self, scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "detail": {
                    "message": "request body exceeds the configured limit",
                    "reason": "request_body_too_large",
                    "limit_bytes": limit,
                }
            },
        )
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.path_limits.get(scope.get("path", ""), self.max_bytes)
        headers = dict(scope.get("headers", []))
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                declared = int(raw_length)
            except ValueError:
                declared = 0
            if declared > limit:
                await self._reject(scope, receive, send, limit)
                return

        buffered: list[Message] = []
        total = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                break
            total += len(message.get("body", b""))
            if total > limit:
                await self._reject(scope, receive, send, limit)
                return
            if not message.get("more_body", False):
                break

        position = 0

        async def replay() -> Message:
            nonlocal position
            if position < len(buffered):
                message = buffered[position]
                position += 1
                return message
            return await receive()

        await self.app(scope, replay, send)
