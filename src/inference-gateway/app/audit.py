"""Redacted request fingerprints and tamper-evident audit-chain primitives."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import Request

from app.settings import message_prompt_chars

AUDIT_GENESIS = hashlib.sha256(b"genesis").hexdigest()


def chain_audit_event(request: Request, event: dict[str, Any]) -> None:
    """Hash-link an audit event into the current gateway process chain."""
    state = request.app.state
    previous = getattr(state, "audit_prev_hash", AUDIT_GENESIS)
    canonical = json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")
    record_hash = hashlib.sha256(previous.encode("ascii") + canonical).hexdigest()
    event["prev_hash"] = previous
    event["record_hash"] = record_hash
    state.audit_prev_hash = record_hash


def payload_fingerprint(payload: dict[str, Any]) -> dict[str, Any]:
    """Summarize a payload into redacted counts and complete canonical hashes."""
    messages = payload.get("messages") or []
    raw_text_field = payload.get("input")
    if raw_text_field is None:
        raw_text_field = payload.get("prompt")
    if not messages and raw_text_field is not None:
        texts = [str(item) for item in (raw_text_field if isinstance(raw_text_field, list) else [raw_text_field])]
        canonical = json.dumps(texts, sort_keys=True, separators=(",", ":"))
        result = {
            "input_count": len(texts),
            "prompt_chars": sum(len(text) for text in texts),
            "prompt_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        }
        request_canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        result["request_sha256"] = hashlib.sha256(request_canonical.encode("utf-8")).hexdigest()
        return result

    canonical_messages = []
    roles = []
    tool_call_count = 0
    for message in messages:
        role = str(message.get("role", "unknown"))
        roles.append(role)
        canonical_messages.append(message)
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            tool_call_count += len(tool_calls)
    canonical_prompt: dict[str, Any] = {"messages": canonical_messages}
    for field in ("tools", "functions", "tool_choice", "function_call", "response_format"):
        if field in payload:
            canonical_prompt[field] = payload[field]
    canonical = json.dumps(canonical_prompt, sort_keys=True, separators=(",", ":"), default=str)
    request_canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    fingerprint: dict[str, Any] = {
        "message_count": len(messages),
        "message_roles": roles,
        "prompt_chars": message_prompt_chars(messages),
        "prompt_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "request_sha256": hashlib.sha256(request_canonical.encode("utf-8")).hexdigest(),
    }
    if payload.get("tools"):
        fingerprint["tool_count"] = len(payload["tools"])
    if tool_call_count:
        fingerprint["tool_call_count"] = tool_call_count
    return fingerprint
