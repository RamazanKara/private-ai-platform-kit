"""Admission-policy constants and payload inspection helpers."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

SANDBOX_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
BUILT_IN_SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "private_key": re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    # Canonical AWS access-key-id shape (as used by GitHub secret scanning). A 20-char
    # all-caps/digit token beginning AKIA/ASIA can false-positive; that is the accepted
    # trade for catching leaked keys, and redact mode makes it non-fatal for coding traffic.
    "aws_access_key_id": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "bearer_token": re.compile(
        r"\b(?:authorization|bearer)\s*[:=]\s*bearer\s+[A-Za-z0-9._~+/=-]{20,}\b",
        re.IGNORECASE,
    ),
    "generic_api_key_assignment": re.compile(
        r"\b(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=-]{20,}['\"]?",
        re.IGNORECASE,
    ),
    # PII patterns. Not enabled by default (see DEFAULT_SECRET_PATTERNS) because emails
    # are common in legitimate prompts; opt in via PROMPT_SECRET_PATTERNS / the chart.
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "us_ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
}

# Patterns enabled by default: credential detectors only. PII detectors are built in
# but opt-in so existing prompt behavior is unchanged unless an operator enables them.
DEFAULT_SECRET_PATTERNS: tuple[str, ...] = (
    "private_key",
    "github_token",
    "slack_token",
    "aws_access_key_id",
    "google_api_key",
    "bearer_token",
    "generic_api_key_assignment",
)
CREDENTIAL_PATTERN_NAMES = frozenset(DEFAULT_SECRET_PATTERNS)
# Modes for prompt secret handling: reject the request, redact the matched spans before
# forwarding, or allow-and-record. ``block`` preserves the historical fail-closed default.
PROMPT_SECRET_MODES = frozenset({"block", "redact", "flag"})
PII_PATTERN_NAMES = frozenset({"email", "us_ssn", "credit_card"})

# Patterns scanned on the model's *output* (the response path) when the output guardrail
# is enabled: every credential detector plus the PII detectors. Unlike prompt admission
# (credentials only by default), output inspection defaults to scanning PII too because a
# completion that leaks an SSN/card/email back to the caller is the OWASP LLM02:2025
# sensitive-information-disclosure failure the output guardrail exists to catch.
OUTPUT_DEFAULT_PATTERNS: tuple[str, ...] = (
    "private_key",
    "github_token",
    "slack_token",
    "aws_access_key_id",
    "google_api_key",
    "bearer_token",
    "generic_api_key_assignment",
    "email",
    "us_ssn",
    "credit_card",
)
OUTPUT_GUARDRAIL_MODES = frozenset({"flag", "redact", "block"})

# Batch endpoints a /v1/batches job may target (ADR 0011); mirrors OpenAI's allowed set,
# scoped to the inference routes this gateway governs.
BATCH_ALLOWED_ENDPOINTS = frozenset({"/v1/chat/completions", "/v1/completions", "/v1/embeddings"})


def iter_payload_strings(value: Any) -> Iterator[str]:
    """Yield every string leaf in caller-controlled JSON.

    Tool definitions, tool-call arguments, legacy function calls, and provider
    extension fields are forwarded to a model and can carry the same secrets or
    blocked content as ``message.content``. Recursion keeps admission aligned with
    the complete forwarded payload instead of an incomplete field allowlist.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from iter_payload_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_payload_strings(item)


def payload_string_chars(value: Any) -> int:
    """Return aggregate characters across every string leaf in a JSON value."""
    return sum(len(text) for text in iter_payload_strings(value))


def message_prompt_chars(messages: list[Any]) -> int:
    """Count chat content plus model-visible tool/function-call arguments."""
    total = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        total += len(extract_text_content(message.get("content")))
        total += payload_string_chars(message.get("tool_calls"))
        total += payload_string_chars(message.get("function_call"))
    return total


def extract_text_content(content: Any) -> str:
    """Return the plain text of a chat message ``content`` field.

    Accepts a string, ``None``, or an OpenAI-style content-part array (each part a
    mapping with a ``text`` field, e.g. ``{"type": "text", "text": "..."}``); non-text
    parts such as ``image_url`` contribute no characters. Used by admission sizing,
    secret scanning, and audit fingerprinting so multimodal requests are handled
    without assuming ``content`` is a bare string.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return str(content)


def completion_prompt_texts(prompt: Any) -> list[str]:
    """Return the legacy-completion ``prompt`` as a list of non-empty strings.

    Accepts a bare string or a list of strings (the two forms OpenAI's ``/v1/completions``
    ``prompt`` takes); token-array prompts and other shapes contribute no text. Empty
    strings are dropped so an all-empty prompt reads as missing.
    """
    if isinstance(prompt, str):
        return [prompt] if prompt else []
    if isinstance(prompt, list):
        return [item for item in prompt if isinstance(item, str) and item]
    return []


def max_requested_completion_tokens(payload: dict[str, Any]) -> int | None:
    """Return the largest valid completion-token cap the caller requested, or None.

    OpenAI's modern ``max_completion_tokens`` and the legacy ``max_tokens`` may both be
    present and both are forwarded to the runtime; budget estimation charges the larger so
    it upper-bounds whatever field the backend actually honors. An explicit ``0`` (the
    embeddings path passes ``max_tokens=0`` to mean "no completion cost") is honored;
    non-integer values are ignored. Returns None when no valid integer field is present.
    """
    values = [
        value
        for field in ("max_completion_tokens", "max_tokens")
        if isinstance((value := payload.get(field)), int) and not isinstance(value, bool) and value >= 0
    ]
    return max(values) if values else None


def requested_completion_count(payload: dict[str, Any]) -> Any:
    """Return the caller's requested number of completions (``n``), defaulting to 1.

    Returns the raw value (possibly a non-int, for the validator to reject).
    """
    value = payload.get("n")
    return 1 if value is None else value


def _image_part_bytes(part: dict[str, Any]) -> int:
    """Estimate the decoded byte size of an OpenAI ``image_url`` part (data URLs only).

    Remote (http/https) image URLs carry no local bytes and count as zero; a
    ``data:`` URL is measured from its base64 payload (3 bytes per 4 chars).
    """
    image = part.get("image_url")
    url = image.get("url") if isinstance(image, dict) else image
    if not isinstance(url, str) or not url.startswith("data:"):
        return 0
    _, _, b64 = url.partition(",")
    return (len(b64) * 3) // 4


def _iter_image_parts(messages: list[Any]) -> Any:
    """Yield every OpenAI ``image_url`` content part across the given messages."""
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    yield part


def count_image_parts(messages: list[Any]) -> int:
    """Count OpenAI ``image_url`` content parts across all messages."""
    return sum(1 for _ in _iter_image_parts(messages))


def largest_image_bytes(messages: list[Any]) -> int:
    """Return the largest decoded data-URL image byte size across all messages."""
    return max((_image_part_bytes(part) for part in _iter_image_parts(messages)), default=0)


def validate_sandbox_id(value: str) -> str:
    """Normalize and validate a sandbox id, raising ValueError when malformed."""
    sandbox_id = value.strip().lower()
    if not SANDBOX_ID_PATTERN.fullmatch(sandbox_id):
        raise ValueError("sandbox id must be 1-63 characters of lowercase letters, numbers, or hyphens")
    return sandbox_id


class AdmissionPolicyError(ValueError):
    """Raised when a request violates an admission policy, carrying a machine reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class ModelPolicyError(AdmissionPolicyError):
    """Raised when a requested model is not approved by policy."""
