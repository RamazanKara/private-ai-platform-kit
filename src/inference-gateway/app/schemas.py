"""Request models for the gateway's OpenAI-compatible endpoints."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class Message(BaseModel):
    """A single chat message with an OpenAI-style role and content.

    ``content`` accepts a plain string, an OpenAI-style content-part array (text
    and ``image_url`` parts, enabling vision-capable runtimes), or ``null`` for an
    assistant turn that only carries ``tool_calls``. ``extra="allow"`` lets any
    additional OpenAI message fields pass through to the runtime unchanged.
    """

    model_config = ConfigDict(extra="allow")

    role: Literal["system", "developer", "user", "assistant", "tool", "function"]
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class ChatCompletionRequest(BaseModel):
    """Request body for an OpenAI-compatible chat completion call.

    Tool/function-calling and structured-output fields are modelled explicitly so
    they survive ``model_dump`` to the runtime (the flagship coding-agent path),
    and ``extra="allow"`` forwards any other OpenAI sampling parameter (``top_p``,
    ``stop``, ``seed``, ``stream_options``, ...) verbatim instead of silently
    dropping it.
    """

    model_config = ConfigDict(extra="allow")

    model: str | None = None
    messages: list[Message]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool | None = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    functions: list[dict[str, Any]] | None = None
    function_call: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None


class EmbeddingsRequest(BaseModel):
    """Request body for an OpenAI-compatible embeddings call.

    Routing embeddings through the gateway (rather than calling a separate embedding
    service directly) subjects them to the same auth, model allowlist, budget, and
    audit controls as chat completions. ``extra="allow"`` forwards provider params
    such as ``dimensions`` or ``encoding_format`` unchanged.
    """

    model_config = ConfigDict(extra="allow")

    model: str | None = None
    input: str | list[str]


class CompletionRequest(BaseModel):
    """Request body for an OpenAI-compatible legacy text completion call.

    The pre-chat ``/v1/completions`` API takes a ``prompt`` (a string or list of strings)
    rather than ``messages``. Routing it through the gateway subjects legacy-completion
    traffic to the same auth, allowlist, admission, budget, and audit controls as chat.
    ``extra="allow"`` forwards any other OpenAI sampling parameter to the runtime verbatim.
    """

    model_config = ConfigDict(extra="allow")

    model: str | None = None
    prompt: str | list[str]
    max_tokens: int | None = None
    stream: bool | None = False


class ModerationRequest(BaseModel):
    """Request body for an OpenAI-compatible moderations call."""

    model_config = ConfigDict(extra="allow")

    input: str | list[str]
    model: str | None = None


class BatchRequest(BaseModel):
    """A batch of chat-completion requests processed in one call.

    Each item runs through the same auth (the batch is one authenticated request), model
    allowlist, admission, and budget controls; items are processed concurrently and the
    response reports per-item success or error so one bad item does not fail the batch.
    """

    model_config = ConfigDict(extra="allow")

    requests: list[ChatCompletionRequest]
