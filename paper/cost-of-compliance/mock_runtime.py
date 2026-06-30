#!/usr/bin/env python3
"""OpenAI-compatible mock runtime as a raw asyncio HTTP/1.1 server.

Why raw asyncio instead of an ASGI app behind uvicorn: the inference gateway opens
its upstream connection with a default httpx client (Nagle enabled). On the loopback
with tiny payloads, Nagle on the sender plus delayed ACK on the receiver injects a
~40 ms stall per request that has nothing to do with the gateway's real work and
would not occur against a real runtime on a separate node. Owning the accepted socket
lets us set TCP_NODELAY and TCP_QUICKACK on the mock side, which breaks that
interaction and keeps the measured latency attributable to the gateway.

The server understands exactly two routes:
  GET  /healthz, /health        -> {"status": "ok"}
  POST /v1/chat/completions     -> a fixed OpenAI-compatible completion

An optional fixed think-time (MOCK_DELAY_MS) stands in for inference time.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import socket
from time import time

DELAY_SECONDS = float(os.environ.get("MOCK_DELAY_MS", "0")) / 1000.0


def _completion(model: str, prompt_chars: int) -> bytes:
    payload = {
        "id": f"chatcmpl-coc-{int(time() * 1000)}",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello from the mock runtime."},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": max(1, prompt_chars // 4),
            "completion_tokens": 8,
            "total_tokens": max(1, prompt_chars // 4) + 8,
        },
    }
    return json.dumps(payload).encode("utf-8")


def _http_response(status: str, body: bytes, keep_alive: bool) -> bytes:
    headers = [
        f"HTTP/1.1 {status}",
        "Content-Type: application/json",
        f"Content-Length: {len(body)}",
        f"Connection: {'keep-alive' if keep_alive else 'close'}",
    ]
    return ("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + body


def _arm_quickack(sock: socket.socket) -> None:
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if hasattr(socket, "TCP_QUICKACK"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_QUICKACK, 1)
    except OSError:
        pass


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    sock = writer.get_extra_info("socket")
    try:
        while True:
            if sock is not None:
                _arm_quickack(sock)
            try:
                request_line = await reader.readuntil(b"\r\n\r\n")
            except (asyncio.IncompleteReadError, ConnectionResetError):
                break
            head = request_line.decode("latin-1")
            first = head.split("\r\n", 1)[0]
            parts = first.split(" ")
            method = parts[0] if parts else ""
            path = parts[1] if len(parts) > 1 else "/"
            content_length = 0
            keep_alive = True
            for line in head.split("\r\n")[1:]:
                if not line:
                    continue
                name, _, value = line.partition(":")
                name = name.strip().lower()
                value = value.strip()
                if name == "content-length":
                    content_length = int(value or "0")
                elif name == "connection" and value.lower() == "close":
                    keep_alive = False

            body = b""
            remaining = content_length
            while remaining > 0:
                chunk = await reader.read(remaining)
                if not chunk:
                    break
                body += chunk
                remaining -= len(chunk)

            if method == "GET" and path in ("/healthz", "/health"):
                writer.write(_http_response("200 OK", b'{"status":"ok"}', keep_alive))
            elif method == "POST" and path == "/v1/chat/completions":
                prompt_chars = 0
                model = "mock-model"
                try:
                    payload = json.loads(body or b"{}")
                    messages = payload.get("messages", [])
                    prompt_chars = sum(
                        len(str(m.get("content", ""))) for m in messages if isinstance(m, dict)
                    )
                    model = str(payload.get("model") or "mock-model")
                except (ValueError, AttributeError):
                    pass
                if DELAY_SECONDS > 0:
                    await asyncio.sleep(DELAY_SECONDS)
                writer.write(_http_response("200 OK", _completion(model, prompt_chars), keep_alive))
            else:
                writer.write(_http_response("404 Not Found", b'{"detail":"not found"}', keep_alive))

            await writer.drain()
            if not keep_alive:
                break
    finally:
        with contextlib.suppress(OSError):
            writer.close()


async def serve(host: str, port: int) -> None:
    server = await asyncio.start_server(handle, host, port)
    async with server:
        await server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Raw asyncio OpenAI-compatible mock runtime.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--delay-ms", type=float, default=None)
    args = parser.parse_args()
    global DELAY_SECONDS
    if args.delay_ms is not None:
        DELAY_SECONDS = args.delay_ms / 1000.0
    asyncio.run(serve(args.host, args.port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
