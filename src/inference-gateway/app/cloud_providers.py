"""Cloud wire formats; transport, admission, budgets, and receipts stay in the gateway."""

from __future__ import annotations

import json
import os
import struct
import zlib
from collections.abc import AsyncIterator
from time import time
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

from app.policy import ModelRoute
from app.settings import AdmissionPolicyError


def credential_headers(route: ModelRoute) -> dict[str, str]:
    credential = os.environ.get(route.credential_env, "")
    if not credential or "\n" in credential or "\r" in credential:
        raise AdmissionPolicyError("provider_not_configured", "provider credential is unavailable")
    if route.backend == "anthropic":
        return {"x-api-key": credential, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {credential}"}


def cloud_request(
    route: ModelRoute, payload: dict[str, Any], endpoint: str, max_tokens: int
) -> tuple[str, dict[str, Any], dict[str, str]]:
    headers = credential_headers(route)
    body = dict(payload)
    body["model"] = route.upstream_model
    body.pop("data_classification", None)
    if endpoint != "chat/completions" and route.backend in {"anthropic", "bedrock", "vertex"}:
        raise AdmissionPolicyError("provider_endpoint_not_supported", "provider supports chat generation only")
    if route.backend not in {"anthropic", "bedrock"}:
        return f"{route.base_url}/{endpoint}", body, headers
    if payload.get("n", 1) != 1:
        raise AdmissionPolicyError("provider_parameter_not_supported", "provider requires n=1")
    # Refuse semantics we cannot translate rather than silently changing an agent's request.
    supported = {
        "model",
        "messages",
        "max_tokens",
        "max_completion_tokens",
        "temperature",
        "top_p",
        "stop",
        "stream",
        "stream_options",
        "tools",
        "tool_choice",
        "n",
    }
    if route.backend == "anthropic":
        supported.add("top_k")
    if set(body) - supported:
        raise AdmissionPolicyError("provider_parameter_not_supported", "parameter has no supported provider mapping")
    anthropic = route.backend == "anthropic"
    messages: list[dict[str, Any]] = []
    system: list[dict[str, Any]] = []
    for message in payload["messages"]:
        role = message["role"]
        content = message.get("content") or ""
        parts = [{"type": "text", "text": content}] if isinstance(content, str) else content
        if any(part.get("type") != "text" for part in parts):
            raise AdmissionPolicyError("provider_content_not_supported", "provider adapter accepts text and tools only")
        blocks = [
            ({"type": "text", "text": part["text"]} if anthropic else {"text": part["text"]})
            for part in parts
            if part.get("text")
        ]
        if role in {"system", "developer"}:
            system.extend(blocks)
            continue
        if role == "tool":
            if anthropic:
                blocks = [{"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": blocks}]
            else:
                blocks = [{"toolResult": {"toolUseId": message["tool_call_id"], "content": blocks}}]
            role = "user"
        elif role not in {"user", "assistant"}:
            raise AdmissionPolicyError("provider_content_not_supported", "provider adapter does not support this role")
        for tool in message.get("tool_calls") or []:
            function = tool["function"]
            try:
                arguments = json.loads(function["arguments"])
            except (ValueError, TypeError) as exc:
                raise AdmissionPolicyError("invalid_tool_arguments", "tool arguments must be a JSON object") from exc
            if not isinstance(arguments, dict):
                raise AdmissionPolicyError("invalid_tool_arguments", "tool arguments must be a JSON object")
            if anthropic:
                blocks.append({"type": "tool_use", "id": tool["id"], "name": function["name"], "input": arguments})
            else:
                blocks.append({"toolUse": {"toolUseId": tool["id"], "name": function["name"], "input": arguments}})
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].extend(blocks)
        else:
            messages.append({"role": role, "content": blocks})
    limit = payload.get("max_completion_tokens", payload.get("max_tokens", max_tokens))
    config: dict[str, Any] = {"max_tokens" if anthropic else "maxTokens": limit}
    for source, target in (("temperature", "temperature"), ("top_p", "topP"), ("stop", "stopSequences")):
        if source in payload:
            value = payload[source]
            if source == "stop" and isinstance(value, str):
                value = [value]
            config[{"top_p": "top_p", "stop": "stop_sequences"}.get(source, source) if anthropic else target] = value
    body = (
        {"model": route.upstream_model, "messages": messages, **config}
        if anthropic
        else {"messages": messages, "inferenceConfig": config}
    )
    if system:
        body["system"] = system
    if anthropic:
        body["stream"] = bool(payload.get("stream"))
        if "top_k" in payload:
            body["top_k"] = payload["top_k"]
    tools = []
    for tool in payload.get("tools") or []:
        function = tool["function"]
        definition = {"name": function["name"], "description": function.get("description", "")}
        schema = function.get("parameters", {"type": "object", "properties": {}})
        definition["input_schema" if anthropic else "inputSchema"] = schema if anthropic else {"json": schema}
        tools.append(definition if anthropic else {"toolSpec": definition})
    choice = payload.get("tool_choice", "auto")
    if tools and choice != "none":
        if isinstance(choice, dict):
            name = choice["function"]["name"]
            tool_choice = {"type": "tool", "name": name} if anthropic else {"tool": {"name": name}}
        else:
            mode = "any" if choice == "required" else "auto"
            tool_choice = {"type": mode} if anthropic else {mode: {}}
        if anthropic:
            body.update(tools=tools, tool_choice=tool_choice)
        else:
            body["toolConfig"] = {"tools": tools, "toolChoice": tool_choice}
    endpoint = "messages" if anthropic else f"model/{quote(route.upstream_model, safe='')}/converse"
    if not anthropic and payload.get("stream"):
        endpoint += "-stream"
    return f"{route.base_url}/{endpoint}", body, headers


def normalized_usage(usage: Any, backend: str) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    if backend == "anthropic":
        names = ("input_tokens", "output_tokens")
    elif backend == "bedrock":
        names = ("inputTokens", "outputTokens")
    else:
        names = ("prompt_tokens", "completion_tokens")
    prompt, completion = (usage.get(name, 0) for name in names)
    if not any(name in usage for name in names):
        return None
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (prompt, completion)):
        raise ValueError("invalid provider usage")
    if backend == "anthropic":
        for name in ("cache_creation_input_tokens", "cache_read_input_tokens"):
            value = usage.get(name, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("invalid provider usage")
            prompt += value
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


def finish_reason(reason: str | None) -> str:
    return {
        "max_tokens": "length",
        "tool_use": "tool_calls",
        "guardrail_intervened": "content_filter",
        "content_filtered": "content_filter",
        "refusal": "content_filter",
    }.get(reason or "", "stop")


def cloud_response(data: dict[str, Any], route: ModelRoute) -> dict[str, Any]:
    if route.backend not in {"anthropic", "bedrock"}:
        if not isinstance(data.get("choices", data.get("data")), list):
            raise ValueError("invalid provider response")
        data = dict(data)
        data["model"] = route.model_id
        data["usage"] = normalized_usage(data.get("usage"), route.backend)
        return data
    anthropic = route.backend == "anthropic"
    blocks = data.get("content") if anthropic else data.get("output", {}).get("message", {}).get("content")
    if not isinstance(blocks, list):
        raise ValueError("invalid provider response")
    text = ""
    calls = []
    for block in blocks:
        if "text" in block and (not anthropic or block.get("type") == "text"):
            text += block["text"]
        tool = block if anthropic and block.get("type") == "tool_use" else block.get("toolUse")
        if tool:
            calls.append(
                {
                    "id": tool["id" if anthropic else "toolUseId"],
                    "type": "function",
                    "function": {"name": tool["name"], "arguments": json.dumps(tool["input"])},
                }
            )
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = calls
    return {
        "id": data.get("id", f"chatcmpl-{uuid4().hex}"),
        "object": "chat.completion",
        "created": int(time()),
        "model": route.model_id,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason(data.get("stop_reason" if anthropic else "stopReason")),
            }
        ],
        "usage": normalized_usage(data.get("usage"), route.backend),
    }


async def _bedrock_events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    pending = b""
    async for chunk in response.aiter_bytes():
        pending += chunk
        while len(pending) >= 12:
            length, header_length, checksum = struct.unpack(">III", pending[:12])
            if length < 16 or length > 16 * 1024 * 1024 or header_length > length - 16:
                raise ValueError("invalid provider event frame")
            if zlib.crc32(pending[:8]) != checksum:
                raise ValueError("invalid provider event checksum")
            if len(pending) < length:
                break
            frame, pending = pending[:length], pending[length:]
            if zlib.crc32(frame[:-4]) != struct.unpack(">I", frame[-4:])[0]:
                raise ValueError("invalid provider event checksum")
            headers = {}
            offset = 12
            end = 12 + header_length
            while offset < end:
                size = frame[offset]
                offset += 1
                name = frame[offset : offset + size].decode()
                offset += size
                if frame[offset] != 7:
                    raise ValueError("invalid provider event header")
                size = struct.unpack(">H", frame[offset + 1 : offset + 3])[0]
                offset += 3
                headers[name] = frame[offset : offset + size].decode()
                offset += size
            if offset != end or headers.get(":message-type") != "event":
                raise ValueError("provider stream error")
            event = json.loads(frame[end:-4])
            yield {"type": headers.get(":event-type"), **event}
    if pending:
        raise ValueError("truncated provider event frame")


async def _sse_events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if raw == "[DONE]":
            yield {"type": "done"}
            return
        event = json.loads(raw)
        if not isinstance(event, dict) or "error" in event:
            raise ValueError("provider stream error")
        yield event


async def cloud_stream(response: httpx.Response, route: ModelRoute) -> AsyncIterator[bytes]:
    native = route.backend in {"anthropic", "bedrock"}
    events = _bedrock_events(response) if route.backend == "bedrock" else _sse_events(response)
    usage: dict[str, Any] = {}
    complete = False
    stream_id = f"chatcmpl-{uuid4().hex}"
    tool_indexes: dict[int, int] = {}
    try:
        async for event in events:
            kind = event.get("type")
            if not native:
                if kind == "done":
                    complete = True
                    break
                event["model"] = route.model_id
                if event.get("usage"):
                    event["usage"] = normalized_usage(event["usage"], route.backend)
                yield f"data: {json.dumps(event)}\n\n".encode()
                continue
            delta: dict[str, Any] = {}
            reason = None
            reported = None
            if kind in {"message_start", "messageStart"}:
                usage.update(event.get("message", {}).get("usage", {}))
                delta = {"role": "assistant"}
            elif kind in {"content_block_start", "contentBlockStart"}:
                block = event.get("content_block", event.get("start", {}))
                tool = block if block.get("type") == "tool_use" else block.get("toolUse")
                if tool:
                    index = event.get("index", event.get("contentBlockIndex", 0))
                    tool_indexes[index] = len(tool_indexes)
                    delta = {
                        "tool_calls": [
                            {
                                "index": tool_indexes[index],
                                "id": tool.get("id", tool.get("toolUseId")),
                                "type": "function",
                                "function": {"name": tool["name"], "arguments": ""},
                            }
                        ]
                    }
                elif block.get("text"):
                    delta = {"content": block["text"]}
            elif kind in {"content_block_delta", "contentBlockDelta"}:
                block = event["delta"]
                if "text" in block:
                    delta = {"content": block["text"]}
                if "partial_json" in block or "toolUse" in block:
                    index = event.get("index", event.get("contentBlockIndex", 0))
                    arguments = block.get("partial_json", block.get("toolUse", {}).get("input", ""))
                    delta = {"tool_calls": [{"index": tool_indexes[index], "function": {"arguments": arguments}}]}
            elif kind in {"message_delta", "messageStop"}:
                usage.update(event.get("usage", {}))
                reason = finish_reason(event.get("delta", {}).get("stop_reason", event.get("stopReason")))
            elif kind in {"message_stop", "metadata"}:
                usage.update(event.get("usage", {}))
                reported = normalized_usage(usage, route.backend)
                complete = True
            if delta or reason or reported is not None:
                chunk = {
                    "id": stream_id,
                    "object": "chat.completion.chunk",
                    "created": int(time()),
                    "model": route.model_id,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": reason}] if delta or reason else [],
                }
                if reported is not None:
                    chunk["usage"] = reported
                yield f"data: {json.dumps(chunk)}\n\n".encode()
        if not complete:
            raise ValueError("incomplete provider stream")
    except (ValueError, KeyError, TypeError, IndexError, struct.error) as exc:
        raise httpx.RemoteProtocolError("invalid provider stream") from exc
    yield b"data: [DONE]\n\n"
