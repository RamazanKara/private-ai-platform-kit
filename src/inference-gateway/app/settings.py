"""Inference gateway settings, admission policy, and environment configuration loading."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
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
# completion that leaks an SSN/card/email back to the caller is the exact OWASP LLM06
# "sensitive information disclosure" failure the output guardrail exists to catch.
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


def _float_from_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _int_from_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be zero or greater")
    return value


def _positive_int_from_env(name: str, default: int) -> int:
    value = _int_from_env(name, default)
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _positive_float_from_env(name: str, default: float) -> float:
    value = _float_from_env(name, default)
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _bool_from_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def parse_completion_window(value: str) -> int:
    """Return the batch completion window in seconds, or raise ValueError on a bad format.

    Accepts a positive integer with an ``h`` (hours), ``m`` (minutes), or ``s`` (seconds)
    suffix, e.g. ``24h``. OpenAI only documents ``24h``; the wider grammar lets local test
    windows be short without a special case. The window is honored as an expiry bound.
    """
    normalized = value.strip()
    # Bound and parse directly: this value is caller controlled on the Batch API,
    # so it must not enter a repetition-bearing regular expression or an
    # unbounded integer conversion.
    if len(normalized) < 2 or len(normalized) > 13:
        raise ValueError("batch_completion_window must look like '24h', '30m', or '90s'")
    amount_text, suffix = normalized[:-1], normalized[-1]
    if suffix not in {"h", "m", "s"} or not amount_text.isascii() or not amount_text.isdigit():
        raise ValueError("batch_completion_window must look like '24h', '30m', or '90s'")
    amount = int(amount_text)
    if amount <= 0:
        raise ValueError("batch_completion_window must be greater than zero")
    return amount * {"h": 3600, "m": 60, "s": 1}[suffix]


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


def _csv_from_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _sha256s_from_env(name: str) -> tuple[str, ...]:
    hashes = _csv_from_env(name, ())
    for item in hashes:
        if not re.fullmatch(r"[\da-fA-F]{64}", item):
            raise ValueError(f"{name} must contain comma-separated SHA-256 hex digests")
    return tuple(item.lower() for item in hashes)


def _secret_pattern_names_from_env(name: str) -> tuple[str, ...]:
    names = _csv_from_env(name, DEFAULT_SECRET_PATTERNS)
    unknown = sorted(set(names) - set(BUILT_IN_SECRET_PATTERNS))
    if unknown:
        raise ValueError(f"{name} contains unknown built-in secret patterns: {unknown}")
    return names


def _output_pattern_names_from_env(name: str) -> tuple[str, ...]:
    names = _csv_from_env(name, OUTPUT_DEFAULT_PATTERNS)
    unknown = sorted(set(names) - set(BUILT_IN_SECRET_PATTERNS))
    if unknown:
        raise ValueError(f"{name} contains unknown built-in secret patterns: {unknown}")
    return names


class AdmissionPolicyError(ValueError):
    """Raised when a request violates an admission policy, carrying a machine reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class ModelPolicyError(AdmissionPolicyError):
    """Raised when a requested model is not approved by policy."""


@dataclass(frozen=True)
class Settings:
    """Immutable gateway configuration with admission and runtime policy parameters."""

    runtime_backend: str
    ollama_base_url: str
    vllm_base_url: str
    model_id: str
    request_timeout_seconds: float
    audit_log_enabled: bool = True
    default_sandbox_id: str = "local-lab"
    allowed_models: tuple[str, ...] = ()
    max_messages: int = 16
    max_prompt_chars: int = 8192
    max_request_body_bytes: int = 1048576
    max_completion_tokens: int = 1024
    max_completions_per_request: int = 1
    image_part_token_estimate: int = 768
    max_image_bytes: int = 0
    max_tools: int = 64
    max_tool_chars: int = 32768
    allow_streaming: bool = False
    sandbox_budget_enabled: bool = False
    sandbox_request_budget: int = 0
    sandbox_prompt_char_budget: int = 0
    sandbox_estimated_token_budget: int = 0
    budget_estimated_chars_per_token: int = 4
    sandbox_budget_backend: str = "memory"
    sandbox_budget_redis_url: str = "redis://budget-redis.budget.svc.cluster.local:6379/0"
    sandbox_budget_redis_timeout_seconds: float = 0.5
    sandbox_budget_window_seconds: int = 86400
    sandbox_budget_key_prefix: str = "private-ai-platform-kit:sandbox-budget"
    rate_limit_enabled: bool = False
    rate_limit_requests_per_window: int = 0
    rate_limit_window_seconds: int = 60
    # Deliberate availability-vs-enforcement tradeoff. When the shared rate-limit backend
    # (Redis) is unreachable the limiter fails CLOSED by default (503 for all traffic),
    # matching the budget tracker. Set this true to fail OPEN instead: admit the request
    # with a logged warning and a metric so an operator can prefer availability over the
    # throttle during a Redis outage. Budgets stay fail-closed regardless of this flag.
    rate_limit_fail_open: bool = False
    max_concurrent_requests: int = 0
    max_batch_requests: int = 32
    usd_per_1k_tokens: float = 0.0
    cost_currency: str = "USD"
    response_cache_enabled: bool = False
    response_cache_ttl_seconds: int = 60
    response_cache_max_entries: int = 1024
    response_cache_backend: str = "memory"
    response_cache_redis_url: str = "redis://budget-redis.budget.svc.cluster.local:6379/1"
    response_cache_redis_timeout_seconds: float = 0.5
    response_cache_key_prefix: str = "private-ai-platform-kit:response-cache"
    output_guardrail_enabled: bool = False
    output_guardrail_mode: str = "redact"
    output_guardrail_patterns: tuple[str, ...] = OUTPUT_DEFAULT_PATTERNS
    api_key_auth_enabled: bool = False
    api_key_sha256s: tuple[str, ...] = ()
    api_key_header: str = "X-API-Key"
    api_key_records_path: Path | None = None
    prompt_secret_detection_enabled: bool = True
    prompt_secret_mode: str = "block"
    prompt_secret_patterns: tuple[str, ...] = DEFAULT_SECRET_PATTERNS
    blocked_content_terms: tuple[str, ...] = ()
    model_routing_policy_path: Path | None = None
    sandbox_policy_path: Path | None = None
    jwt_auth_enabled: bool = False
    jwt_jwks_url: str = ""
    jwt_issuer: str = ""
    jwt_audience: str = ""
    jwt_required_scopes: tuple[str, ...] = ()
    jwt_cache_seconds: int = 300
    jwt_tenant_claim: str = ""
    runtime_max_retries: int = 2
    runtime_retry_backoff_seconds: float = 0.1
    runtime_circuit_failure_threshold: int = 0
    runtime_circuit_reset_seconds: float = 30.0
    otel_tracing_enabled: bool = False
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "inference-gateway"
    # Asynchronous Files + Batch API (ADR 0011). Off by default; enabling it requires an object
    # store for blobs and a Redis-backed store + queue for durable job state.
    batch_api_enabled: bool = False
    batch_object_store_backend: str = "filesystem"
    batch_object_store_root: str = "/var/lib/inference-gateway/batch"
    batch_s3_endpoint_url: str = ""
    batch_s3_bucket: str = ""
    batch_s3_region: str = "us-east-1"
    batch_s3_access_key_id: str = ""
    batch_s3_secret_access_key: str = ""
    batch_store_backend: str = "memory"
    batch_redis_url: str = "redis://budget-redis.budget.svc.cluster.local:6379/2"
    batch_redis_timeout_seconds: float = 0.5
    batch_key_prefix: str = "private-ai-platform-kit:batch"
    batch_max_file_bytes: int = 104857600
    batch_max_requests_per_batch: int = 50000
    batch_completion_window: str = "24h"
    batch_retention_seconds: int = 604800
    # Server-side Responses API state (ADR 0012). Off by default; storing responses persists raw
    # conversation content (opt-in, tenant-scoped, TTL-bounded).
    responses_store_enabled: bool = False
    responses_store_backend: str = "memory"
    responses_redis_url: str = "redis://budget-redis.budget.svc.cluster.local:6379/3"
    responses_redis_timeout_seconds: float = 0.5
    responses_key_prefix: str = "private-ai-platform-kit:responses"
    responses_retention_seconds: int = 86400
    # Opt-in read-only admin console served at /console (ADR 0013). Off by default.
    admin_console_enabled: bool = False

    def __post_init__(self) -> None:
        """Validate budget, auth, JWT, and runtime resilience fields after init."""
        for name, value in (
            ("sandbox_request_budget", self.sandbox_request_budget),
            ("sandbox_prompt_char_budget", self.sandbox_prompt_char_budget),
            ("sandbox_estimated_token_budget", self.sandbox_estimated_token_budget),
        ):
            if value < 0:
                raise ValueError(f"{name} must be zero or greater")
        if self.budget_estimated_chars_per_token <= 0:
            raise ValueError("budget_estimated_chars_per_token must be greater than zero")
        if self.sandbox_budget_backend not in {"memory", "redis"}:
            raise ValueError("sandbox_budget_backend must be either 'memory' or 'redis'")
        if self.sandbox_budget_window_seconds < 0:
            raise ValueError("sandbox_budget_window_seconds must be zero or greater")
        if self.sandbox_budget_redis_timeout_seconds <= 0:
            raise ValueError("sandbox_budget_redis_timeout_seconds must be greater than zero")
        if self.rate_limit_requests_per_window < 0:
            raise ValueError("rate_limit_requests_per_window must be zero or greater")
        if self.rate_limit_window_seconds <= 0:
            raise ValueError("rate_limit_window_seconds must be greater than zero")
        if self.max_concurrent_requests < 0:
            raise ValueError("max_concurrent_requests must be zero or greater")
        if self.max_batch_requests <= 0:
            raise ValueError("max_batch_requests must be greater than zero")
        if self.max_request_body_bytes <= 0:
            raise ValueError("max_request_body_bytes must be greater than zero")
        if self.max_completions_per_request <= 0:
            raise ValueError("max_completions_per_request must be greater than zero")
        if self.image_part_token_estimate < 0:
            raise ValueError("image_part_token_estimate must be zero or greater")
        if self.max_image_bytes < 0:
            raise ValueError("max_image_bytes must be zero or greater")
        if self.usd_per_1k_tokens < 0:
            raise ValueError("usd_per_1k_tokens must be zero or greater")
        if self.response_cache_ttl_seconds <= 0:
            raise ValueError("response_cache_ttl_seconds must be greater than zero")
        if self.response_cache_max_entries <= 0:
            raise ValueError("response_cache_max_entries must be greater than zero")
        if self.response_cache_backend not in {"memory", "redis"}:
            raise ValueError("response_cache_backend must be either 'memory' or 'redis'")
        if self.response_cache_redis_timeout_seconds <= 0:
            raise ValueError("response_cache_redis_timeout_seconds must be greater than zero")
        if not self.response_cache_key_prefix.strip():
            raise ValueError("response_cache_key_prefix must not be empty")
        if self.output_guardrail_mode not in OUTPUT_GUARDRAIL_MODES:
            raise ValueError("output_guardrail_mode must be one of: flag, redact, block")
        unknown_output_patterns = sorted(set(self.output_guardrail_patterns) - set(BUILT_IN_SECRET_PATTERNS))
        if unknown_output_patterns:
            raise ValueError(f"output_guardrail_patterns contains unknown patterns: {unknown_output_patterns}")
        if not self.sandbox_budget_key_prefix.strip():
            raise ValueError("sandbox_budget_key_prefix must not be empty")
        if self.api_key_auth_enabled and not self.api_key_sha256s and self.api_key_records_path is None:
            # Records-only auth is valid (a key-records file with no flat hashes), so require
            # at least one key source rather than the flat list specifically. An empty records
            # file is still fail-closed at load time via KeyRecordSet.from_path.
            raise ValueError("api_key_sha256s or api_key_records_path must be set when API key auth is enabled")
        for item in self.api_key_sha256s:
            if not re.fullmatch(r"[\da-f]{64}", item):
                raise ValueError("api_key_sha256s must contain SHA-256 hex digests")
        if not self.api_key_header.strip():
            raise ValueError("api_key_header must not be empty")
        if self.jwt_auth_enabled and not self.jwt_jwks_url:
            raise ValueError("jwt_jwks_url must be set when JWT auth is enabled")
        if self.jwt_cache_seconds <= 0:
            raise ValueError("jwt_cache_seconds must be greater than zero")
        if self.runtime_max_retries < 0:
            raise ValueError("runtime_max_retries must be zero or greater")
        if self.runtime_retry_backoff_seconds <= 0:
            raise ValueError("runtime_retry_backoff_seconds must be greater than zero")
        if self.runtime_circuit_failure_threshold < 0:
            raise ValueError("runtime_circuit_failure_threshold must be zero or greater")
        if self.runtime_circuit_reset_seconds <= 0:
            raise ValueError("runtime_circuit_reset_seconds must be greater than zero")
        if self.otel_tracing_enabled and not self.otel_exporter_otlp_endpoint:
            raise ValueError("otel_exporter_otlp_endpoint must be set when OTEL tracing is enabled")
        unknown_patterns = sorted(set(self.prompt_secret_patterns) - set(BUILT_IN_SECRET_PATTERNS))
        if unknown_patterns:
            raise ValueError(f"prompt_secret_patterns contains unknown patterns: {unknown_patterns}")
        if self.prompt_secret_mode not in PROMPT_SECRET_MODES:
            raise ValueError("prompt_secret_mode must be one of: block, redact, flag")
        if self.batch_object_store_backend not in {"filesystem", "memory", "s3"}:
            raise ValueError("batch_object_store_backend must be one of: filesystem, memory, s3")
        if self.batch_store_backend not in {"memory", "redis"}:
            raise ValueError("batch_store_backend must be either 'memory' or 'redis'")
        if self.batch_redis_timeout_seconds <= 0:
            raise ValueError("batch_redis_timeout_seconds must be greater than zero")
        if self.batch_max_file_bytes <= 0:
            raise ValueError("batch_max_file_bytes must be greater than zero")
        if self.batch_max_requests_per_batch <= 0:
            raise ValueError("batch_max_requests_per_batch must be greater than zero")
        if self.batch_retention_seconds <= 0:
            raise ValueError("batch_retention_seconds must be greater than zero")
        if not self.batch_key_prefix.strip():
            raise ValueError("batch_key_prefix must not be empty")
        if self.batch_api_enabled and self.batch_object_store_backend == "s3" and not self.batch_s3_bucket:
            raise ValueError("batch_s3_bucket must be set when the batch object store backend is s3")
        parse_completion_window(self.batch_completion_window)
        if self.responses_store_backend not in {"memory", "redis"}:
            raise ValueError("responses_store_backend must be either 'memory' or 'redis'")
        if self.responses_redis_timeout_seconds <= 0:
            raise ValueError("responses_redis_timeout_seconds must be greater than zero")
        if self.responses_retention_seconds <= 0:
            raise ValueError("responses_retention_seconds must be greater than zero")
        if not self.responses_key_prefix.strip():
            raise ValueError("responses_key_prefix must not be empty")

    @classmethod
    def from_env(cls) -> Settings:
        """Construct settings from environment variables with validated defaults."""
        backend = os.getenv("RUNTIME_BACKEND", "ollama").strip().lower()
        if backend not in {"ollama", "vllm"}:
            raise ValueError("RUNTIME_BACKEND must be either 'ollama' or 'vllm'")
        model_id = os.getenv("MODEL_ID", "qwen3.5:0.8b")
        return cls(
            runtime_backend=backend,
            ollama_base_url=os.getenv(
                "OLLAMA_BASE_URL",
                "http://ollama.ollama.svc.cluster.local:11434",
            ).rstrip("/"),
            vllm_base_url=os.getenv(
                "VLLM_BASE_URL",
                "http://vllm.vllm.svc.cluster.local:8000",
            ).rstrip("/"),
            model_id=model_id,
            request_timeout_seconds=_float_from_env("REQUEST_TIMEOUT_SECONDS", 120.0),
            audit_log_enabled=_bool_from_env("AUDIT_LOG_ENABLED", True),
            default_sandbox_id=validate_sandbox_id(os.getenv("DEFAULT_SANDBOX_ID", "local-lab")),
            allowed_models=_csv_from_env("ALLOWED_MODELS", (model_id,)),
            max_messages=_int_from_env("MAX_MESSAGES", 16),
            max_prompt_chars=_int_from_env("MAX_PROMPT_CHARS", 8192),
            max_request_body_bytes=_positive_int_from_env("MAX_REQUEST_BODY_BYTES", 1048576),
            max_completion_tokens=_int_from_env("MAX_COMPLETION_TOKENS", 1024),
            max_completions_per_request=_positive_int_from_env("MAX_COMPLETIONS_PER_REQUEST", 1),
            image_part_token_estimate=_int_from_env("IMAGE_PART_TOKEN_ESTIMATE", 768),
            max_image_bytes=_int_from_env("MAX_IMAGE_BYTES", 0),
            max_tools=_int_from_env("MAX_TOOLS", 64),
            max_tool_chars=_int_from_env("MAX_TOOL_CHARS", 32768),
            allow_streaming=_bool_from_env("ALLOW_STREAMING", False),
            sandbox_budget_enabled=_bool_from_env("SANDBOX_BUDGET_ENABLED", False),
            sandbox_request_budget=_int_from_env("SANDBOX_REQUEST_BUDGET", 0),
            sandbox_prompt_char_budget=_int_from_env("SANDBOX_PROMPT_CHAR_BUDGET", 0),
            sandbox_estimated_token_budget=_int_from_env(
                "SANDBOX_ESTIMATED_TOKEN_BUDGET",
                0,
            ),
            budget_estimated_chars_per_token=_positive_int_from_env(
                "BUDGET_ESTIMATED_CHARS_PER_TOKEN",
                4,
            ),
            sandbox_budget_backend=os.getenv("SANDBOX_BUDGET_BACKEND", "memory").strip().lower(),
            sandbox_budget_redis_url=os.getenv(
                "SANDBOX_BUDGET_REDIS_URL",
                "redis://budget-redis.budget.svc.cluster.local:6379/0",
            ),
            sandbox_budget_redis_timeout_seconds=_float_from_env(
                "SANDBOX_BUDGET_REDIS_TIMEOUT_SECONDS",
                0.5,
            ),
            sandbox_budget_window_seconds=_int_from_env(
                "SANDBOX_BUDGET_WINDOW_SECONDS",
                86400,
            ),
            sandbox_budget_key_prefix=os.getenv(
                "SANDBOX_BUDGET_KEY_PREFIX",
                "private-ai-platform-kit:sandbox-budget",
            ),
            rate_limit_enabled=_bool_from_env("RATE_LIMIT_ENABLED", False),
            rate_limit_requests_per_window=_int_from_env("RATE_LIMIT_REQUESTS_PER_WINDOW", 0),
            rate_limit_window_seconds=_positive_int_from_env("RATE_LIMIT_WINDOW_SECONDS", 60),
            rate_limit_fail_open=_bool_from_env("RATE_LIMIT_FAIL_OPEN", False),
            max_concurrent_requests=_int_from_env("MAX_CONCURRENT_REQUESTS", 0),
            max_batch_requests=_positive_int_from_env("MAX_BATCH_REQUESTS", 32),
            usd_per_1k_tokens=_float_from_env("USD_PER_1K_TOKENS", 0.0),
            cost_currency=os.getenv("COST_CURRENCY", "USD").strip() or "USD",
            response_cache_enabled=_bool_from_env("RESPONSE_CACHE_ENABLED", False),
            response_cache_ttl_seconds=_positive_int_from_env("RESPONSE_CACHE_TTL_SECONDS", 60),
            response_cache_max_entries=_positive_int_from_env("RESPONSE_CACHE_MAX_ENTRIES", 1024),
            response_cache_backend=os.getenv("RESPONSE_CACHE_BACKEND", "memory").strip().lower(),
            response_cache_redis_url=os.getenv(
                "RESPONSE_CACHE_REDIS_URL",
                "redis://budget-redis.budget.svc.cluster.local:6379/1",
            ),
            response_cache_redis_timeout_seconds=_float_from_env(
                "RESPONSE_CACHE_REDIS_TIMEOUT_SECONDS",
                0.5,
            ),
            response_cache_key_prefix=os.getenv(
                "RESPONSE_CACHE_KEY_PREFIX",
                "private-ai-platform-kit:response-cache",
            ),
            output_guardrail_enabled=_bool_from_env("OUTPUT_GUARDRAIL_ENABLED", False),
            output_guardrail_mode=os.getenv("OUTPUT_GUARDRAIL_MODE", "redact").strip().lower(),
            output_guardrail_patterns=_output_pattern_names_from_env("OUTPUT_GUARDRAIL_PATTERNS"),
            api_key_auth_enabled=_bool_from_env("API_KEY_AUTH_ENABLED", False),
            api_key_sha256s=_sha256s_from_env("API_KEY_SHA256S"),
            api_key_header=os.getenv("API_KEY_HEADER", "X-API-Key"),
            api_key_records_path=_path_from_env("API_KEY_RECORDS_PATH"),
            prompt_secret_detection_enabled=_bool_from_env(
                "PROMPT_SECRET_DETECTION_ENABLED",
                True,
            ),
            prompt_secret_mode=os.getenv("PROMPT_SECRET_MODE", "block").strip().lower(),
            prompt_secret_patterns=_secret_pattern_names_from_env("PROMPT_SECRET_PATTERNS"),
            blocked_content_terms=_csv_from_env("BLOCKED_CONTENT_TERMS", ()),
            model_routing_policy_path=_path_from_env("MODEL_ROUTING_POLICY_PATH"),
            sandbox_policy_path=_path_from_env("SANDBOX_POLICY_PATH"),
            jwt_auth_enabled=_bool_from_env("JWT_AUTH_ENABLED", False),
            jwt_jwks_url=os.getenv("JWT_JWKS_URL", "").strip(),
            jwt_issuer=os.getenv("JWT_ISSUER", "").strip(),
            jwt_audience=os.getenv("JWT_AUDIENCE", "").strip(),
            jwt_required_scopes=_csv_from_env("JWT_REQUIRED_SCOPES", ()),
            jwt_cache_seconds=_positive_int_from_env("JWT_CACHE_SECONDS", 300),
            jwt_tenant_claim=os.getenv("JWT_TENANT_CLAIM", "").strip(),
            runtime_max_retries=_int_from_env("RUNTIME_MAX_RETRIES", 2),
            runtime_retry_backoff_seconds=_positive_float_from_env(
                "RUNTIME_RETRY_BACKOFF_SECONDS",
                0.1,
            ),
            runtime_circuit_failure_threshold=_int_from_env(
                "RUNTIME_CIRCUIT_FAILURE_THRESHOLD",
                0,
            ),
            runtime_circuit_reset_seconds=_positive_float_from_env(
                "RUNTIME_CIRCUIT_RESET_SECONDS",
                30.0,
            ),
            otel_tracing_enabled=_bool_from_env("OTEL_TRACING_ENABLED", False),
            otel_exporter_otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip(),
            otel_service_name=os.getenv("OTEL_SERVICE_NAME", "inference-gateway").strip(),
            batch_api_enabled=_bool_from_env("BATCH_API_ENABLED", False),
            batch_object_store_backend=os.getenv("BATCH_OBJECT_STORE_BACKEND", "filesystem").strip().lower(),
            batch_object_store_root=os.getenv("BATCH_OBJECT_STORE_ROOT", "/var/lib/inference-gateway/batch"),
            batch_s3_endpoint_url=os.getenv("BATCH_S3_ENDPOINT_URL", "").rstrip("/"),
            batch_s3_bucket=os.getenv("BATCH_S3_BUCKET", "").strip(),
            batch_s3_region=os.getenv("BATCH_S3_REGION", "us-east-1").strip(),
            batch_s3_access_key_id=os.getenv("BATCH_S3_ACCESS_KEY_ID", ""),
            batch_s3_secret_access_key=os.getenv("BATCH_S3_SECRET_ACCESS_KEY", ""),
            batch_store_backend=os.getenv("BATCH_STORE_BACKEND", "memory").strip().lower(),
            batch_redis_url=os.getenv("BATCH_REDIS_URL", "redis://budget-redis.budget.svc.cluster.local:6379/2"),
            batch_redis_timeout_seconds=_float_from_env("BATCH_REDIS_TIMEOUT_SECONDS", 0.5),
            batch_key_prefix=os.getenv("BATCH_KEY_PREFIX", "private-ai-platform-kit:batch"),
            batch_max_file_bytes=_positive_int_from_env("BATCH_MAX_FILE_BYTES", 104857600),
            batch_max_requests_per_batch=_positive_int_from_env("BATCH_MAX_REQUESTS_PER_BATCH", 50000),
            batch_completion_window=os.getenv("BATCH_COMPLETION_WINDOW", "24h").strip(),
            batch_retention_seconds=_positive_int_from_env("BATCH_RETENTION_SECONDS", 604800),
            responses_store_enabled=_bool_from_env("RESPONSES_STORE_ENABLED", False),
            responses_store_backend=os.getenv("RESPONSES_STORE_BACKEND", "memory").strip().lower(),
            responses_redis_url=os.getenv(
                "RESPONSES_REDIS_URL", "redis://budget-redis.budget.svc.cluster.local:6379/3"
            ),
            responses_redis_timeout_seconds=_float_from_env("RESPONSES_REDIS_TIMEOUT_SECONDS", 0.5),
            responses_key_prefix=os.getenv("RESPONSES_KEY_PREFIX", "private-ai-platform-kit:responses"),
            responses_retention_seconds=_positive_int_from_env("RESPONSES_RETENTION_SECONDS", 86400),
            admin_console_enabled=_bool_from_env("ADMIN_CONSOLE_ENABLED", False),
        )

    @property
    def runtime_base_url(self) -> str:
        """Return the base URL of the currently configured runtime backend."""
        if self.runtime_backend == "vllm":
            return self.vllm_base_url
        return self.ollama_base_url

    def validate_model(self, requested_model: str | None) -> str:
        """Return the model to use, raising ModelPolicyError if it is not allowed."""
        model = requested_model or self.model_id
        if self.allowed_models and model not in self.allowed_models:
            raise ModelPolicyError(
                "model_not_allowed",
                f"model '{model}' is not in ALLOWED_MODELS",
            )
        return model

    def validate_admission(self, payload: dict) -> None:
        """Enforce model, message, prompt, secret, token, and streaming admission rules."""
        self.validate_model(payload.get("model"))
        messages = payload.get("messages") or []
        if not messages:
            raise AdmissionPolicyError(
                "missing_messages",
                "request must include at least one message",
            )
        if len(messages) > self.max_messages:
            raise AdmissionPolicyError(
                "too_many_messages",
                f"request has {len(messages)} messages; limit is {self.max_messages}",
            )
        # Complete message objects count toward the prompt ceiling. Assistant
        # tool-call and legacy function-call arguments become prompt context on
        # the next turn and must not bypass admission through non-content fields.
        prompt_chars = message_prompt_chars(messages)
        if prompt_chars > self.max_prompt_chars:
            raise AdmissionPolicyError(
                "prompt_too_large",
                f"prompt has {prompt_chars} characters; limit is {self.max_prompt_chars}",
            )
        self._validate_tools(payload)
        for text in iter_payload_strings(payload):
            self._enforce_content_policy(text)
        # Validate BOTH completion-cap fields independently: the request forwards both to
        # the runtime, and different runtimes honor different fields (vLLM prefers
        # max_completion_tokens, Ollama honors max_tokens), so a cap on only the "preferred"
        # field would let the other slip an uncapped value through to the backend.
        for field in ("max_completion_tokens", "max_tokens"):
            value = payload.get(field)
            if value is None:
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise AdmissionPolicyError(
                    "invalid_max_tokens",
                    "max_tokens/max_completion_tokens must be a positive integer",
                )
            if value > self.max_completion_tokens:
                raise AdmissionPolicyError(
                    "max_tokens_too_large",
                    f"requested completion tokens is {value}; limit is {self.max_completion_tokens}",
                )
        requested_completions = payload.get("n")
        if requested_completions is not None:
            if (
                not isinstance(requested_completions, int)
                or isinstance(requested_completions, bool)
                or requested_completions <= 0
            ):
                raise AdmissionPolicyError("invalid_n", "n must be a positive integer")
            if requested_completions > self.max_completions_per_request:
                raise AdmissionPolicyError(
                    "too_many_completions",
                    f"n is {requested_completions}; limit is {self.max_completions_per_request}",
                )
        if self.max_image_bytes > 0:
            oversized = largest_image_bytes(messages)
            if oversized > self.max_image_bytes:
                raise AdmissionPolicyError(
                    "image_too_large",
                    f"an image part is ~{oversized} bytes; limit is {self.max_image_bytes}",
                )
        temperature = payload.get("temperature")
        if temperature is not None:
            try:
                normalized_temperature = float(temperature)
            except (TypeError, ValueError) as exc:
                raise AdmissionPolicyError(
                    "invalid_temperature",
                    "temperature must be between 0 and 2",
                ) from exc
            if not 0 <= normalized_temperature <= 2:
                raise AdmissionPolicyError(
                    "invalid_temperature",
                    "temperature must be between 0 and 2",
                )
        if payload.get("stream") and not self.allow_streaming:
            raise AdmissionPolicyError(
                "streaming_disabled",
                "streaming responses are disabled for this gateway",
            )

    def validate_embedding_admission(self, payload: dict) -> None:
        """Enforce model, input-size, and secret rules for an embeddings request.

        Embeddings now route through the gateway, so the same model allowlist, prompt
        size limit, and credential-pattern rejection apply to embedding inputs.
        """
        self.validate_model(payload.get("model"))
        raw = payload.get("input")
        texts = raw if isinstance(raw, list) else [raw]
        texts = [str(item) for item in texts if item is not None and str(item) != ""]
        if not texts:
            raise AdmissionPolicyError(
                "missing_input",
                "embeddings request must include non-empty input",
            )
        total_chars = sum(len(text) for text in texts)
        if total_chars > self.max_prompt_chars:
            raise AdmissionPolicyError(
                "prompt_too_large",
                f"embedding input has {total_chars} characters; limit is {self.max_prompt_chars}",
            )
        for text in iter_payload_strings(payload):
            self._enforce_content_policy(text)

    def validate_completion_admission(self, payload: dict) -> None:
        """Enforce model, prompt-size, secret, token, and streaming rules for legacy completions.

        The legacy ``/v1/completions`` API carries a ``prompt`` (a string or a list of
        strings) instead of ``messages``; this applies the same ceilings as chat: model
        allowlist, aggregate prompt-character limit, secret/blocked-term content policy,
        completion-token cap, and the streaming toggle.
        """
        self.validate_model(payload.get("model"))
        prompt_texts = completion_prompt_texts(payload.get("prompt"))
        if not prompt_texts:
            raise AdmissionPolicyError(
                "missing_prompt",
                "completions request must include a non-empty prompt",
            )
        prompt_chars = sum(len(text) for text in prompt_texts)
        if prompt_chars > self.max_prompt_chars:
            raise AdmissionPolicyError(
                "prompt_too_large",
                f"prompt has {prompt_chars} characters; limit is {self.max_prompt_chars}",
            )
        for text in iter_payload_strings(payload):
            self._enforce_content_policy(text)
        for field in ("max_completion_tokens", "max_tokens"):
            value = payload.get(field)
            if value is None:
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise AdmissionPolicyError(
                    "invalid_max_tokens",
                    "max_tokens/max_completion_tokens must be a positive integer",
                )
            if value > self.max_completion_tokens:
                raise AdmissionPolicyError(
                    "max_tokens_too_large",
                    f"requested completion tokens is {value}; limit is {self.max_completion_tokens}",
                )
        requested_completions = payload.get("n")
        if requested_completions is not None:
            if (
                not isinstance(requested_completions, int)
                or isinstance(requested_completions, bool)
                or requested_completions <= 0
            ):
                raise AdmissionPolicyError("invalid_n", "n must be a positive integer")
            if requested_completions > self.max_completions_per_request:
                raise AdmissionPolicyError(
                    "too_many_completions",
                    f"n is {requested_completions}; limit is {self.max_completions_per_request}",
                )
        if payload.get("stream") and not self.allow_streaming:
            raise AdmissionPolicyError(
                "streaming_disabled",
                "streaming responses are disabled for this gateway",
            )

    def matched_secret_pattern(self, text: str) -> str | None:
        """Return the name of the first configured secret/PII pattern matched, or None."""
        for pattern_name in self.prompt_secret_patterns:
            if BUILT_IN_SECRET_PATTERNS[pattern_name].search(text):
                return pattern_name
        return None

    def matched_blocked_term(self, text: str) -> str | None:
        """Return the first configured blocked term contained in the text, or None."""
        if not self.blocked_content_terms:
            return None
        lowered = text.lower()
        for term in self.blocked_content_terms:
            if term and term.lower() in lowered:
                return term
        return None

    def _enforce_content_policy(self, text: str) -> None:
        """Reject text matching the secret detector (block mode only) or a blocked term.

        In ``redact``/``flag`` mode the secret detector does not reject here; the
        request proceeds and :meth:`apply_prompt_secret_mode` redacts or records the
        match before the payload is forwarded. The blocked-term denylist always rejects.
        """
        if self.prompt_secret_detection_enabled and self.prompt_secret_mode == "block":
            pattern = self.matched_secret_pattern(text)
            if pattern is not None:
                raise AdmissionPolicyError(
                    "prompt_secret_detected",
                    f"input appears to contain credential or PII material matched by {pattern}",
                )
        term = self.matched_blocked_term(text)
        if term is not None:
            raise AdmissionPolicyError(
                "content_blocked",
                "input contains content blocked by policy",
            )

    def _redact_secret_text(self, text: str) -> tuple[str, list[str]]:
        """Return the text with matched prompt-secret spans replaced, plus the matched names."""
        matched: list[str] = []
        for name in self.prompt_secret_patterns:
            pattern = BUILT_IN_SECRET_PATTERNS[name]
            if pattern.search(text):
                matched.append(name)
                text = pattern.sub(f"[REDACTED:{name}]", text)
        return text, matched

    def apply_prompt_secret_mode(self, payload: dict[str, Any]) -> list[str]:
        """Redact or flag prompt secrets per ``prompt_secret_mode``; mutate payload in redact mode.

        A no-op in ``block`` mode (admission already rejected) or when detection is
        disabled. Recursively handles every forwarded string, including tool schemas,
        tool/function-call arguments, provider extensions, embedding input, and legacy
        completion prompts. Returns sorted unique pattern names for metrics and audit.
        """
        if not self.prompt_secret_detection_enabled or self.prompt_secret_mode == "block":
            return []
        redact = self.prompt_secret_mode == "redact"
        matched: list[str] = []

        def visit(value: Any) -> Any:
            if isinstance(value, str):
                new_text, names = self._redact_secret_text(value)
                matched.extend(names)
                return new_text if redact and names else value
            if isinstance(value, list):
                return [visit(item) for item in value]
            if isinstance(value, dict):
                return {key: visit(item) for key, item in value.items()}
            return value

        updated = visit(payload)
        if redact and isinstance(updated, dict):
            payload.clear()
            payload.update(updated)
        return sorted(set(matched))

    def output_findings(self, text: str) -> tuple[list[str], list[str]]:
        """Return (matched secret/PII pattern names, matched blocked terms) found in output.

        Scans the model's completion against the configured output-guardrail patterns and
        the blocked-term denylist. Used by the response-path guardrail (OWASP LLM02/LLM06)
        to detect credentials, PII, or denied content the model emitted back to the caller.
        """
        patterns = [name for name in self.output_guardrail_patterns if BUILT_IN_SECRET_PATTERNS[name].search(text)]
        terms: list[str] = []
        if self.blocked_content_terms:
            lowered = text.lower()
            terms = [term for term in self.blocked_content_terms if term and term.lower() in lowered]
        return patterns, terms

    def redact_output_text(self, text: str) -> tuple[str, list[str]]:
        """Return the text with matched secrets/PII/blocked terms replaced, plus what matched.

        Each configured pattern that matches is substituted with ``[REDACTED:<name>]`` and
        each blocked term with ``[REDACTED]``, so a leaked credential never reaches the
        caller while the surrounding completion is preserved.
        """
        matched: list[str] = []
        for name in self.output_guardrail_patterns:
            pattern = BUILT_IN_SECRET_PATTERNS[name]
            if pattern.search(text):
                matched.append(name)
                text = pattern.sub(f"[REDACTED:{name}]", text)
        for term in self.blocked_content_terms:
            if term and term.lower() in text.lower():
                matched.append(f"term:{term}")
                text = re.sub(re.escape(term), "[REDACTED]", text, flags=re.IGNORECASE)
        return text, matched

    def _validate_tools(self, payload: dict) -> None:
        """Bound tool/function definitions by count and serialized size.

        Tool schemas are attacker-influenced free-form JSON forwarded to the
        runtime; caps keep a caller from smuggling an oversized payload past the
        prompt-character limit via the ``tools``/``functions`` fields.
        """
        for field in ("tools", "functions"):
            value = payload.get(field)
            if value is None:
                continue
            if not isinstance(value, list):
                raise AdmissionPolicyError("invalid_tools", f"{field} must be a list")
            if len(value) > self.max_tools:
                raise AdmissionPolicyError(
                    "too_many_tools",
                    f"request defines {len(value)} {field}; limit is {self.max_tools}",
                )
            serialized_chars = len(json.dumps(value, default=str))
            if serialized_chars > self.max_tool_chars:
                raise AdmissionPolicyError(
                    "tools_too_large",
                    f"{field} serialize to {serialized_chars} characters; limit is {self.max_tool_chars}",
                )


def _path_from_env(name: str) -> Path | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return Path(raw.strip())


def moderate_text(text: str, settings: Settings) -> dict[str, Any]:
    """Classify text against credential, PII, and blocked-term policy (rule-based).

    Independent of the admission config: scans every built-in credential and PII
    detector plus the configured blocked terms, returning an OpenAI ``/v1/moderations``
    shaped result. This is a deterministic content-policy surface; a semantic toxicity
    classifier can be layered behind the same endpoint without changing callers.
    """
    credential = any(BUILT_IN_SECRET_PATTERNS[name].search(text) for name in CREDENTIAL_PATTERN_NAMES)
    pii = any(BUILT_IN_SECRET_PATTERNS[name].search(text) for name in PII_PATTERN_NAMES)
    blocked = settings.matched_blocked_term(text) is not None
    categories = {"credential": credential, "pii": pii, "blocked_terms": blocked}
    return {
        "flagged": any(categories.values()),
        "categories": categories,
        "category_scores": {key: (1.0 if value else 0.0) for key, value in categories.items()},
    }
