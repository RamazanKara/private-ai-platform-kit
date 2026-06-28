"""RAG service settings and environment configuration loading with validation."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

SANDBOX_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
VALID_RETRIEVAL_BACKENDS = {"lexical", "qdrant"}
VALID_EMBEDDING_PROVIDERS = {"hash", "openai-compatible"}
DEFAULT_VECTOR_DIMENSIONS = 384


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


def _positive_int_from_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _positive_float_from_env_first(names: tuple[str, ...], default: float) -> float:
    for name in names:
        raw = os.getenv(name)
        if raw is not None:
            try:
                value = float(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be a number") from exc
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")
            return value
    return default


def _env_first(names: tuple[str, ...], default: str) -> str:
    for name in names:
        raw = os.getenv(name)
        if raw is not None:
            return raw
    return default


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


def validate_sandbox_id(value: str) -> str:
    """Normalize and validate a sandbox id, raising ValueError when malformed."""
    sandbox_id = value.strip().lower()
    if not SANDBOX_ID_PATTERN.fullmatch(sandbox_id):
        raise ValueError("sandbox id must be 1-63 characters of lowercase letters, numbers, or hyphens")
    return sandbox_id


@dataclass(frozen=True)
class Settings:
    """Immutable RAG service configuration for retrieval, embeddings, and auth."""

    document_dir: Path
    default_sandbox_id: str = "local-lab"
    audit_log_enabled: bool = True
    max_query_chars: int = 2048
    default_top_k: int = 3
    max_top_k: int = 8
    max_context_chars: int = 6000
    retrieval_backend: str = "lexical"
    vector_store_url: str = ""
    vector_collection: str = "private-ai-platform-kit"
    vector_collection_version: str = "v1"
    vector_timeout_seconds: float = 1.0
    vector_dimensions: int = DEFAULT_VECTOR_DIMENSIONS
    vector_bootstrap_enabled: bool = True
    embedding_provider: str = "hash"
    embedding_base_url: str = ""
    embedding_model: str = "hash-text-v1"
    rag_source_manifest: Path | None = None
    api_key_auth_enabled: bool = False
    api_key_sha256s: tuple[str, ...] = ()
    api_key_header: str = "X-API-Key"
    otel_tracing_enabled: bool = False
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "rag-service"

    def __post_init__(self) -> None:
        """Validate retrieval, vector store, embedding, and auth fields after init."""
        validate_sandbox_id(self.default_sandbox_id)
        for name, value in (
            ("max_query_chars", self.max_query_chars),
            ("default_top_k", self.default_top_k),
            ("max_top_k", self.max_top_k),
            ("max_context_chars", self.max_context_chars),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")
        if self.default_top_k > self.max_top_k:
            raise ValueError("default_top_k must not exceed max_top_k")
        if self.retrieval_backend not in VALID_RETRIEVAL_BACKENDS:
            raise ValueError(f"retrieval_backend must be one of {sorted(VALID_RETRIEVAL_BACKENDS)}")
        if self.retrieval_backend == "qdrant" and not self.vector_store_url:
            raise ValueError("vector_store_url must be set when retrieval_backend is qdrant")
        if not self.vector_collection.strip():
            raise ValueError("vector_collection must not be empty")
        if not self.vector_collection_version.strip():
            raise ValueError("vector_collection_version must not be empty")
        if self.vector_timeout_seconds <= 0:
            raise ValueError("vector_timeout_seconds must be greater than zero")
        if self.vector_dimensions <= 0:
            raise ValueError("vector_dimensions must be greater than zero")
        if self.embedding_provider not in VALID_EMBEDDING_PROVIDERS:
            raise ValueError(f"embedding_provider must be one of {sorted(VALID_EMBEDDING_PROVIDERS)}")
        if self.embedding_provider == "openai-compatible" and not self.embedding_base_url:
            raise ValueError("embedding_base_url must be set when embedding_provider is openai-compatible")
        if self.embedding_provider == "openai-compatible" and not self.embedding_model:
            raise ValueError("embedding_model must be set when embedding_provider is openai-compatible")
        if self.api_key_auth_enabled and not self.api_key_sha256s:
            raise ValueError("api_key_sha256s must be set when API key auth is enabled")
        for item in self.api_key_sha256s:
            if not re.fullmatch(r"[\da-f]{64}", item):
                raise ValueError("api_key_sha256s must contain SHA-256 hex digests")
        if not self.api_key_header.strip():
            raise ValueError("api_key_header must not be empty")
        if self.otel_tracing_enabled and not self.otel_exporter_otlp_endpoint:
            raise ValueError("otel_exporter_otlp_endpoint must be set when OTEL tracing is enabled")

    @classmethod
    def from_env(cls) -> "Settings":
        """Construct settings from environment variables with validated defaults."""
        return cls(
            document_dir=Path(os.getenv("RAG_DOCUMENT_DIR", "/knowledge")),
            default_sandbox_id=validate_sandbox_id(os.getenv("DEFAULT_SANDBOX_ID", "local-lab")),
            audit_log_enabled=_bool_from_env("AUDIT_LOG_ENABLED", True),
            max_query_chars=_positive_int_from_env("MAX_QUERY_CHARS", 2048),
            default_top_k=_positive_int_from_env("DEFAULT_TOP_K", 3),
            max_top_k=_positive_int_from_env("MAX_TOP_K", 8),
            max_context_chars=_positive_int_from_env("MAX_CONTEXT_CHARS", 6000),
            retrieval_backend=_env_first(("RAG_RETRIEVAL_BACKEND", "RETRIEVAL_BACKEND"), "lexical").strip().lower(),
            vector_store_url=_env_first(("QDRANT_URL", "VECTOR_STORE_URL"), "").strip(),
            vector_collection=_env_first(("QDRANT_COLLECTION", "VECTOR_COLLECTION"), "private-ai-platform-kit").strip(),
            vector_collection_version=_env_first(
                ("QDRANT_COLLECTION_VERSION", "VECTOR_COLLECTION_VERSION"), "v1"
            ).strip(),
            vector_timeout_seconds=_positive_float_from_env_first(
                ("QDRANT_TIMEOUT_SECONDS", "VECTOR_TIMEOUT_SECONDS"), 1.0
            ),
            vector_dimensions=_positive_int_from_env("QDRANT_VECTOR_DIMENSIONS", DEFAULT_VECTOR_DIMENSIONS),
            vector_bootstrap_enabled=_bool_from_env("QDRANT_BOOTSTRAP_FROM_KNOWLEDGE", True),
            embedding_provider=os.getenv("RAG_EMBEDDING_PROVIDER", "hash").strip().lower(),
            embedding_base_url=os.getenv("RAG_EMBEDDING_BASE_URL", "").strip(),
            embedding_model=os.getenv("RAG_EMBEDDING_MODEL", "hash-text-v1").strip(),
            rag_source_manifest=_path_from_env("RAG_SOURCE_MANIFEST"),
            api_key_auth_enabled=_bool_from_env("API_KEY_AUTH_ENABLED", False),
            api_key_sha256s=_sha256s_from_env("API_KEY_SHA256S"),
            api_key_header=os.getenv("API_KEY_HEADER", "X-API-Key"),
            otel_tracing_enabled=_bool_from_env("OTEL_TRACING_ENABLED", False),
            otel_exporter_otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip(),
            otel_service_name=os.getenv("OTEL_SERVICE_NAME", "rag-service").strip(),
        )


def _path_from_env(name: str) -> Path | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return Path(raw.strip())
