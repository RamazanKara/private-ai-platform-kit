#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import time


class MockRuntimeHandler(BaseHTTPRequestHandler):
    server_version = "PrivateAIPlatformKitMockRuntime/1.0"

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._write_json(200, {"status": "ok"})
            return
        self._write_json(404, {"detail": "not found"})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self._write_json(404, {"detail": "not found"})
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            self._write_json(400, {"detail": "invalid JSON"})
            return

        messages = payload.get("messages") if isinstance(payload, dict) else []
        prompt_chars = 0
        if isinstance(messages, list):
            prompt_chars = sum(
                len(str(message.get("content", ""))) for message in messages if isinstance(message, dict)
            )
        model = str(payload.get("model") or "mock-model")
        prompt_text = " ".join(
            str(message.get("content", ""))
            for message in messages
            if isinstance(message, dict)
        )
        content = "Hello from the local load-test runtime."
        if "2 + 2" in prompt_text or "2+2" in prompt_text:
            content = "2 + 2 = 4."

        response = {
            "id": f"chatcmpl-loadtest-{int(time() * 1000)}",
            "object": "chat.completion",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": content,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": max(1, prompt_chars // 4),
                "completion_tokens": 8,
                "total_tokens": max(1, prompt_chars // 4) + 8,
            },
        }
        self._write_json(200, response)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenAI-compatible mock runtime for local load tests.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), MockRuntimeHandler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
