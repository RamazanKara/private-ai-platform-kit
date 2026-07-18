"""OpenAI-compatible server-sent event processing."""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request

from app.runtime_client import REDACTED_MESSAGE_FIELDS


def _usage_from_sse_chunk(chunk: bytes) -> dict[str, Any] | None:
    """Return the ``usage`` object from a terminal SSE chunk, or None when absent.

    OpenAI-compatible streams emit a final ``data:`` event carrying a ``usage``
    object (when usage reporting is enabled) before ``data: [DONE]``. Each chunk may
    contain several SSE events; scan them and return the last usage object found.

    Any JSON object with a ``usage`` member necessarily contains the literal bytes
    ``"usage"``, so chunks and lines without them are skipped without parsing rather
    than json-decoding every delta event on the event loop.
    """
    if b'"usage"' not in chunk:
        return None
    found: dict[str, Any] | None = None
    for line in chunk.split(b"\n"):
        line = line.strip()
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if not data or data == b"[DONE]" or b'"usage"' not in data:
            continue
        try:
            parsed = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("usage"), dict):
            found = parsed["usage"]
    return found


_STREAM_REASONING_MARKERS: tuple[bytes, ...] = tuple(f'"{field}"'.encode() for field in REDACTED_MESSAGE_FIELDS)


def _is_usage_only_event(obj: dict[str, Any]) -> bool:
    """True when an SSE chunk object carries a usage object but no completion choices.

    This is the shape of the terminal usage event vLLM emits under
    ``stream_options.include_usage``; a normal content chunk that also carries usage
    keeps its choices and is not matched, so real content is never dropped.
    """
    return isinstance(obj.get("usage"), dict) and not obj.get("choices")


def _strip_reasoning_delta(obj: dict[str, Any]) -> bool:
    """Remove reasoning/thinking fields from each choice's delta/message in place.

    Returns True when anything was removed. Mirrors the non-streaming
    ``sanitize_chat_completion`` redaction so chain-of-thought cannot leak through
    the streaming path that a caller reaches by setting ``stream: true``.
    """
    choices = obj.get("choices")
    if not isinstance(choices, list):
        return False
    changed = False
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        for container_key in ("delta", "message"):
            container = choice.get(container_key)
            if isinstance(container, dict):
                for field in REDACTED_MESSAGE_FIELDS:
                    if field in container:
                        del container[field]
                        changed = True
    return changed


def _rewrite_stream_segment(segment: bytes, *, drop_usage_only: bool, strip_reasoning: bool) -> bytes:
    """Filter a complete run of SSE text before it is forwarded to the client.

    ``drop_usage_only`` removes the synthetic usage-only event induced by injecting
    ``stream_options.include_usage`` when the caller did not request usage, so the
    client-facing stream matches what it asked for. ``strip_reasoning`` removes
    reasoning/thinking fields from streamed deltas. Lines triggering neither transform
    are passed through byte-for-byte, keeping the common delta path cheap and lossless.
    """
    need_usage = drop_usage_only and b'"usage"' in segment
    need_reasoning = strip_reasoning and any(marker in segment for marker in _STREAM_REASONING_MARKERS)
    if not need_usage and not need_reasoning:
        return segment
    out: list[bytes] = []
    # When a usage-only event is dropped, also swallow the blank line that terminated it so
    # removing the event does not leave a stray extra separator in the client stream.
    skip_blank = False
    for line in segment.split(b"\n"):
        stripped = line.strip()
        if skip_blank and not stripped:
            skip_blank = False
            continue
        skip_blank = False
        if stripped.startswith(b"data:"):
            data = stripped[5:].strip()
            if data and data != b"[DONE]":
                try:
                    obj = json.loads(data)
                except (ValueError, UnicodeDecodeError):
                    out.append(line)
                    continue
                if isinstance(obj, dict):
                    if need_usage and _is_usage_only_event(obj):
                        skip_blank = True
                        continue
                    if need_reasoning and _strip_reasoning_delta(obj):
                        out.append(b"data: " + json.dumps(obj, separators=(",", ":")).encode("utf-8"))
                        continue
        out.append(line)
    return b"\n".join(out)


def _terminal_stream_error_event(backend: str, request: Request) -> bytes:
    """Build a terminal SSE error event emitted when the upstream fails mid-stream."""
    payload = {
        "error": {
            "message": "runtime stream failed",
            "type": "upstream_error",
            "backend": backend,
            "request_id": request.state.request_id,
            "sandbox_id": request.state.sandbox_id,
        }
    }
    return b"data: " + json.dumps(payload).encode("utf-8") + b"\n\n"
