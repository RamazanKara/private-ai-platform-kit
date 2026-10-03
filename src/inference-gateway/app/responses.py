"""OpenAI Responses API (``/v1/responses``) translation to and from the internal chat path.

The gateway speaks the OpenAI chat-completions protocol end to end (admission, budget,
guardrail, audit). This module is a thin, faithful-but-pragmatic translation layer so a
native OpenAI ``POST /v1/responses`` request runs through the *same* governance path as
chat rather than a second, weaker one: a Responses request is translated into an OpenAI
chat payload here, the caller feeds that payload through the existing chat governance path,
and the resulting OpenAI chat completion is translated back into a Responses object. This
mirrors the Anthropic ``/v1/messages`` translation module (``app/messages.py``) exactly.

Server-side state (``store`` and ``previous_response_id``) is opt-in and handled by
``responses_api``; without the store those fields are rejected rather than ignored.

Multi-turn tool use is translated in both directions: Responses function tools and
``tool_choice`` become their chat shapes, ``function_call`` and ``function_call_output`` input
items become an assistant ``tool_calls`` turn and ``tool`` messages, ``input_image`` parts
become ``image_url`` parts, and assistant ``tool_calls`` come back as ``function_call``
output items. Built-in Responses tools (web search, file search, ...) have no chat
equivalent and are not forwarded.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

# Responses ``status`` for each OpenAI chat ``finish_reason``. ``length`` maps to
# ``incomplete`` (with ``incomplete_details.reason == "max_output_tokens"``); every other
# reason, including ``content_filter`` (the guardrail already rewrote the content) and an
# unknown/absent reason, maps to a ``completed`` response.
_INCOMPLETE_FINISH_REASONS = {"length"}


class ResponsesRequest(BaseModel):
    """Request body for a native OpenAI ``POST /v1/responses`` call.

    Mirrors the OpenAI Responses API shape. ``input`` is required and accepts either a plain
    string (translated to a single user message) or an array of input items / messages.
    ``instructions`` is prepended as a system message. ``max_output_tokens`` becomes the
    OpenAI ``max_tokens`` cap that admission enforces. Fields with no chat equivalent are
    accepted (``extra="allow"``) but not forwarded to the runtime.

    The stateful fields ``store`` and ``previous_response_id`` are modelled explicitly so the
    handler can serve them from the response store, or reject them when the store is off,
    instead of forwarding them into the chat payload where they would be meaningless.
    """

    model_config = ConfigDict(extra="allow")

    model: str | None = None
    input: str | list[Any]
    instructions: str | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    stream: bool | None = False
    metadata: dict[str, Any] | None = None
    store: bool | None = None
    previous_response_id: str | None = None


def _content_part_text(part: Any) -> str | None:
    """Return the text of one Responses content part, or None when it is not a text part.

    Responses content parts are ``{type, ...}`` objects; the input text parts are
    ``input_text`` (and the output side uses ``output_text``). A bare string is treated as
    text. ``text`` is also accepted as a permissive alias so a caller mixing the chat-style
    ``{"type": "text", ...}`` part shape is not silently dropped.
    """
    if isinstance(part, str):
        return part
    if not isinstance(part, dict):
        return None
    if part.get("type") in ("input_text", "output_text", "text"):
        text = part.get("text")
        return text if isinstance(text, str) else ""
    return None


def _content_to_text(content: Any) -> str:
    """Flatten a Responses message ``content`` (string or content-part array) into a string.

    A string is returned as-is. An array's recognized text parts (``input_text`` /
    ``output_text`` / ``text`` / bare strings) are concatenated; non-text parts (e.g. image
    or file parts) contribute nothing to the text projection but do not raise. The chat
    governance path meters on the resulting text.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [text for part in content if (text := _content_part_text(part)) is not None]
        return "".join(parts)
    return str(content)


def _chat_content(content: Any) -> str | list[dict[str, Any]]:
    """Translate Responses message content to chat content, keeping images when present.

    Text-only content flattens to a string (the common case, and what admission meters).
    Content with ``input_image`` parts becomes a chat content-part array so the image reaches
    a vision runtime, subject to the gateway's image-URL and image-size admission rules.
    """
    if not isinstance(content, list) or not any(
        isinstance(part, dict) and part.get("type") == "input_image" for part in content
    ):
        return _content_to_text(content)
    parts: list[dict[str, Any]] = []
    for part in content:
        text = _content_part_text(part)
        if text is not None:
            parts.append({"type": "text", "text": text})
        elif isinstance(part, dict) and part.get("type") == "input_image":
            url = part.get("image_url")
            if isinstance(url, str) and url:
                image: dict[str, Any] = {"url": url}
                if isinstance(part.get("detail"), str):
                    image["detail"] = part["detail"]
                parts.append({"type": "image_url", "image_url": image})
    return parts


def _input_item_to_message(item: Any) -> dict[str, Any] | None:
    """Translate one Responses input item into an OpenAI chat message, or None to skip it.

    Handles a message item ``{role, content}`` (optionally ``{type: "message", ...}``), a
    plain string (a ``user`` message), and a ``function_call_output`` item (a ``tool``
    message answering the call with the same ``call_id``). ``role`` defaults to ``user``.
    ``function_call`` items are merged into the preceding assistant turn by
    :func:`_input_to_messages`; other item types (reasoning, built-in tool calls) carry no
    chat equivalent and are skipped.
    """
    if isinstance(item, str):
        return {"role": "user", "content": item}
    if not isinstance(item, dict):
        return None
    item_type = item.get("type")
    if item_type in (None, "message"):
        role = item.get("role")
        role = str(role) if role else "user"
        return {"role": role, "content": _chat_content(item.get("content"))}
    if item_type == "function_call_output":
        output = item.get("output")
        return {
            "role": "tool",
            "tool_call_id": str(item.get("call_id") or ""),
            "content": output if isinstance(output, str) else json.dumps(output),
        }
    return None


def _function_call_to_tool_call(item: dict[str, Any]) -> dict[str, Any]:
    arguments = item.get("arguments")
    return {
        "id": str(item.get("call_id") or item.get("id") or f"call_{uuid4().hex[:24]}"),
        "type": "function",
        "function": {
            "name": str(item.get("name") or ""),
            "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments or {}),
        },
    }


def _input_to_messages(value: Any) -> list[dict[str, Any]]:
    """Translate a Responses ``input`` (string or item array) into OpenAI chat messages.

    A string becomes a single ``user`` message. An array is translated item by item via
    :func:`_input_item_to_message`. ``function_call`` items (the model's earlier tool calls,
    echoed back by a client that keeps its own history) are attached to the preceding
    assistant message, or to a new content-less assistant message, because chat requires a
    ``tool`` message to follow the assistant turn that issued its call. Any other shape is
    coerced to a single ``user`` message string.
    """
    if isinstance(value, str):
        return [{"role": "user", "content": value}]
    if isinstance(value, list):
        messages: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "function_call":
                last = messages[-1] if messages else None
                if last is None or last.get("role") != "assistant":
                    last = {"role": "assistant", "content": None}
                    messages.append(last)
                last.setdefault("tool_calls", []).append(_function_call_to_tool_call(item))
                continue
            message = _input_item_to_message(item)
            if message is not None:
                messages.append(message)
        return messages
    return [{"role": "user", "content": str(value)}]


def _chat_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate Responses function tools (flat ``{type, name, parameters}``) to chat tools.

    A tool already in the chat shape (with a ``function`` object) passes unchanged. Built-in
    Responses tools (``web_search``, ``file_search``, ...) have no runtime equivalent here and
    are left out rather than forwarded as malformed tool definitions.
    """
    translated: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if isinstance(tool.get("function"), dict):
            translated.append(tool)
        elif tool.get("type") == "function" and tool.get("name"):
            function = {key: tool[key] for key in ("name", "description", "parameters", "strict") if key in tool}
            translated.append({"type": "function", "function": function})
    return translated


def _chat_tool_choice(tool_choice: str | dict[str, Any]) -> str | dict[str, Any]:
    """Translate a Responses ``tool_choice`` naming a function into the chat shape."""
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function" and "name" in tool_choice:
        return {"type": "function", "function": {"name": tool_choice["name"]}}
    return tool_choice


def responses_to_chat_payload(
    request: ResponsesRequest, base_messages: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Translate an OpenAI Responses request into an internal OpenAI chat payload.

    ``base_messages`` (the stored conversation of a ``previous_response_id``) is prepended so a
    chained turn is shown the full prior history; the stateless runtime never has to remember it.

    The returned dict is fed to the *same* chat governance path as ``/v1/chat/completions``
    (allowlist, admission (including the ``max_tokens`` cap and prompt-secret modes on the
    translated messages), budget reserve, runtime call, output guardrail, audit). Mapping:

    - ``instructions`` is prepended as a ``role: "system"`` message.
    - ``input`` is translated to chat messages (a string -> one user message; an item array
      -> a message per ``{role, content}`` / string item, content parts -> text).
    - ``max_output_tokens`` -> ``max_tokens`` (so the completion cap applies); ``temperature``
      / ``top_p`` pass through; function ``tools``, ``tool_choice``, and
      ``parallel_tool_calls`` are translated to their chat shapes.

    ``stream``, ``metadata``, ``store``, and ``previous_response_id`` are intentionally not
    forwarded to the runtime here: the handler decides streaming and rejects the stateful
    fields, and ``metadata`` is a Responses-only field.
    """
    messages: list[dict[str, Any]] = list(base_messages) if base_messages else []
    if request.instructions:
        messages.append({"role": "system", "content": request.instructions})
    messages.extend(_input_to_messages(request.input))

    chat_payload: dict[str, Any] = {"messages": messages}
    if request.model is not None:
        chat_payload["model"] = request.model
    if request.max_output_tokens is not None:
        chat_payload["max_tokens"] = request.max_output_tokens
    if request.temperature is not None:
        chat_payload["temperature"] = request.temperature
    if request.top_p is not None:
        chat_payload["top_p"] = request.top_p
    if request.tools is not None:
        tools = _chat_tools(request.tools)
        if tools:
            chat_payload["tools"] = tools
    if request.tool_choice is not None:
        chat_payload["tool_choice"] = _chat_tool_choice(request.tool_choice)
    parallel = (request.model_extra or {}).get("parallel_tool_calls")
    if isinstance(parallel, bool):
        chat_payload["parallel_tool_calls"] = parallel
    return chat_payload


def _assistant_text(message: dict[str, Any]) -> str:
    """Return the assistant message text of a chat completion, flattening a content array."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [text for part in content if (text := _content_part_text(part)) is not None]
        return "".join(parts)
    return ""


def _function_call_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate assistant ``tool_calls`` into Responses ``function_call`` output items.

    Each OpenAI ``tool_call`` ``{id, function: {name, arguments}}`` becomes a Responses
    ``{type: "function_call", id, call_id, name, arguments, status: "completed"}`` item. The
    ``arguments`` JSON string is preserved verbatim (Responses ``function_call.arguments`` is
    also a JSON string), and ``call_id`` carries the original tool-call id so a follow-up
    ``function_call_output`` can be correlated.
    """
    items: list[dict[str, Any]] = []
    for tool_call in message.get("tool_calls") or []:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") or {}
        call_id = str(tool_call.get("id") or f"call_{uuid4().hex[:24]}")
        arguments = function.get("arguments")
        items.append(
            {
                "type": "function_call",
                "id": f"fc_{uuid4().hex[:24]}",
                "call_id": call_id,
                "name": str(function.get("name") or ""),
                "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments or {}),
                "status": "completed",
            }
        )
    return items


def chat_completion_to_responses(response: dict[str, Any], *, request_model: str | None) -> dict[str, Any]:
    """Translate an OpenAI chat completion into an OpenAI Responses response body.

    Produces ``{id, object:"response", created_at, status, model, output:[...], usage,
    ...}``. The assistant text becomes a ``message`` output item carrying an ``output_text``
    content part; any assistant ``tool_calls`` become ``function_call`` output items
    (best-effort: id/name are preserved and the ``arguments`` JSON string is passed
    through). ``finish_reason`` is mapped to ``status`` (``length`` -> ``incomplete`` with
    ``incomplete_details.reason == "max_output_tokens"``, else ``completed``) and the runtime
    ``usage`` is surfaced as ``input_tokens``/``output_tokens``/``total_tokens``.
    """
    choices = response.get("choices") if isinstance(response, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else {}
    message = choice.get("message") if isinstance(choice, dict) else {}
    if not isinstance(message, dict):
        message = {}

    output: list[dict[str, Any]] = []
    text = _assistant_text(message)
    function_calls = _function_call_items(message)
    # Emit the assistant message item when there is text, or when there are no function calls
    # at all (so an empty completion still yields a well-formed, if empty, message item rather
    # than an output with nothing in it).
    if text or not function_calls:
        output.append(
            {
                "type": "message",
                "id": f"msg_{uuid4().hex[:24]}",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        )
    output.extend(function_calls)

    finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
    if finish_reason in _INCOMPLETE_FINISH_REASONS:
        status = "incomplete"
        incomplete_details: dict[str, Any] | None = {"reason": "max_output_tokens"}
    else:
        status = "completed"
        incomplete_details = None

    usage = response.get("usage") if isinstance(response, dict) else None
    prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
    total_tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    input_tokens = int(prompt_tokens) if isinstance(prompt_tokens, (int, float)) else 0
    output_tokens = int(completion_tokens) if isinstance(completion_tokens, (int, float)) else 0
    total = int(total_tokens) if isinstance(total_tokens, (int, float)) else input_tokens + output_tokens

    response_id = response.get("id") if isinstance(response, dict) else None
    model = response.get("model") if isinstance(response, dict) else None
    created = response.get("created") if isinstance(response, dict) else None

    body: dict[str, Any] = {
        "id": f"resp_{response_id}" if response_id else f"resp_{uuid4().hex[:24]}",
        "object": "response",
        "created_at": int(created) if isinstance(created, (int, float)) else 0,
        "status": status,
        "model": model or request_model or "",
        "output": output,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total,
        },
        "incomplete_details": incomplete_details,
        "error": None,
        "metadata": {},
    }
    return body
