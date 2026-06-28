"""Inference gateway settings, admission policy, and environment configuration loading."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

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
}


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
    names = _csv_from_env(name, tuple(BUILT_IN_SECRET_PATTERNS))
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
    api_key_auth_enabled: bool = False
    api_key_sha256s: tuple[str, ...] = ()
    api_key_header: str = "X-API-Key"
    prompt_secret_detection_enabled: bool = True
    prompt_secret_patterns: tuple[str, ...] = tuple(BUILT_IN_SECRET_PATTERNS)
    model_routing_policy_path: Path | None = None
    sandbox_policy_path: Path | None = None
    jwt_auth_enabled: bool = False
    jwt_jwks_url: str = ""
    jwt_issuer: str = ""
    jwt_audience: str = ""
    jwt_required_scopes: tuple[str, ...] = ()
    jwt_cache_seconds: int = 300
    runtime_max_retries: int = 0
    runtime_retry_backoff_seconds: float = 0.1
    runtime_circuit_failure_threshold: int = 0
    runtime_circuit_reset_seconds: float = 30.0

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
        unknown_patterns = sorted(set(self.prompt_secret_patterns) - set(BUILT_IN_SECRET_PATTERNS))
        if unknown_patterns:
            raise ValueError(f"prompt_secret_patterns contains unknown patterns: {unknown_patterns}")

    @classmethod
    def from_env(cls) -> "Settings":
        """Construct settings from environment variables with validated defaults."""
        backend = os.getenv("RUNTIME_BACKEND", "ollama").strip().lower()
        if backend not in {"ollama", "vllm"}:
            raise ValueError("RUNTIME_BACKEND must be either 'ollama' or 'vllm'")
        model_id = os.getenv("MODEL_ID", "qwen3:0.6b")
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
            api_key_auth_enabled=_bool_from_env("API_KEY_AUTH_ENABLED", False),
            api_key_sha256s=_sha256s_from_env("API_KEY_SHA256S"),
            api_key_header=os.getenv("API_KEY_HEADER", "X-API-Key"),
            prompt_secret_detection_enabled=_bool_from_env(
                "PROMPT_SECRET_DETECTION_ENABLED",
                True,
            ),
            prompt_secret_patterns=_secret_pattern_names_from_env("PROMPT_SECRET_PATTERNS"),
            model_routing_policy_path=_path_from_env("MODEL_ROUTING_POLICY_PATH"),
            sandbox_policy_path=_path_from_env("SANDBOX_POLICY_PATH"),
            jwt_auth_enabled=_bool_from_env("JWT_AUTH_ENABLED", False),
            jwt_jwks_url=os.getenv("JWT_JWKS_URL", "").strip(),
            jwt_issuer=os.getenv("JWT_ISSUER", "").strip(),
            jwt_audience=os.getenv("JWT_AUDIENCE", "").strip(),
            jwt_required_scopes=_csv_from_env("JWT_REQUIRED_SCOPES", ()),
            jwt_cache_seconds=_positive_int_from_env("JWT_CACHE_SECONDS", 300),
            runtime_max_retries=_int_from_env("RUNTIME_MAX_RETRIES", 0),
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
        prompt_chars = sum(len(str(message.get("content", ""))) for message in messages)
        if prompt_chars > self.max_prompt_chars:
            raise AdmissionPolicyError(
                "prompt_too_large",
                f"prompt has {prompt_chars} characters; limit is {self.max_prompt_chars}",
            )
        if self.prompt_secret_detection_enabled:
            for message in messages:
                content = str(message.get("content", ""))
                for pattern_name in self.prompt_secret_patterns:
                    if BUILT_IN_SECRET_PATTERNS[pattern_name].search(content):
                        raise AdmissionPolicyError(
                            "prompt_secret_detected",
                            f"prompt appears to contain credential material matched by {pattern_name}",
                        )
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


def _path_from_env(name: str) -> Path | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return Path(raw.strip())
