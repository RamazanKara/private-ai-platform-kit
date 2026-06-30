"""Inference gateway settings, admission policy, and environment configuration loading."""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SANDBOX_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
BUILT_IN_SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "private_key": re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
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
    "bearer_token",
    "generic_api_key_assignment",
)
CREDENTIAL_PATTERN_NAMES = frozenset(DEFAULT_SECRET_PATTERNS)
PII_PATTERN_NAMES = frozenset({"email", "us_ssn", "credit_card"})


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
    max_completion_tokens: int = 1024
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
    max_concurrent_requests: int = 0
    response_cache_enabled: bool = False
    response_cache_ttl_seconds: int = 60
    response_cache_max_entries: int = 1024
    api_key_auth_enabled: bool = False
    api_key_sha256s: tuple[str, ...] = ()
    api_key_header: str = "X-API-Key"
    prompt_secret_detection_enabled: bool = True
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
        if self.response_cache_ttl_seconds <= 0:
            raise ValueError("response_cache_ttl_seconds must be greater than zero")
        if self.response_cache_max_entries <= 0:
            raise ValueError("response_cache_max_entries must be greater than zero")
        if not self.sandbox_budget_key_prefix.strip():
            raise ValueError("sandbox_budget_key_prefix must not be empty")
        if self.api_key_auth_enabled and not self.api_key_sha256s:
            raise ValueError("api_key_sha256s must be set when API key auth is enabled")
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

    @classmethod
    def from_env(cls) -> "Settings":
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
            max_completion_tokens=_int_from_env("MAX_COMPLETION_TOKENS", 1024),
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
            max_concurrent_requests=_int_from_env("MAX_CONCURRENT_REQUESTS", 0),
            response_cache_enabled=_bool_from_env("RESPONSE_CACHE_ENABLED", False),
            response_cache_ttl_seconds=_positive_int_from_env("RESPONSE_CACHE_TTL_SECONDS", 60),
            response_cache_max_entries=_positive_int_from_env("RESPONSE_CACHE_MAX_ENTRIES", 1024),
            api_key_auth_enabled=_bool_from_env("API_KEY_AUTH_ENABLED", False),
            api_key_sha256s=_sha256s_from_env("API_KEY_SHA256S"),
            api_key_header=os.getenv("API_KEY_HEADER", "X-API-Key"),
            prompt_secret_detection_enabled=_bool_from_env(
                "PROMPT_SECRET_DETECTION_ENABLED",
                True,
            ),
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
        prompt_chars = sum(len(extract_text_content(message.get("content"))) for message in messages)
        if prompt_chars > self.max_prompt_chars:
            raise AdmissionPolicyError(
                "prompt_too_large",
                f"prompt has {prompt_chars} characters; limit is {self.max_prompt_chars}",
            )
        self._validate_tools(payload)
        for message in messages:
            self._enforce_content_policy(extract_text_content(message.get("content")))
        requested_tokens = payload.get("max_tokens")
        if requested_tokens is not None:
            if not isinstance(requested_tokens, int) or requested_tokens <= 0:
                raise AdmissionPolicyError(
                    "invalid_max_tokens",
                    "max_tokens must be a positive integer",
                )
            if requested_tokens > self.max_completion_tokens:
                raise AdmissionPolicyError(
                    "max_tokens_too_large",
                    f"max_tokens is {requested_tokens}; limit is {self.max_completion_tokens}",
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
        for text in texts:
            self._enforce_content_policy(text)

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
        """Reject text that matches the secret/PII detector or a blocked-term denylist."""
        if self.prompt_secret_detection_enabled:
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
