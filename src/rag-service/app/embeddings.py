"""Text embedding providers: deterministic hashing and OpenAI-compatible HTTP backends."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Protocol

import httpx

TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.:/-]*")


class EmbeddingProvider(Protocol):
    """Protocol for embedding providers that map text to a fixed-size vector."""

    name: str
    model: str
    dimensions: int

    def embed(self, value: str) -> list[float]: ...

    async def embed_async(self, value: str) -> list[float]: ...


def tokenize(value: str) -> list[str]:
    """Lowercase the text and return its alphanumeric token sequence."""
    return TOKEN_PATTERN.findall(value.lower())


def hashed_text_embedding(value: str, dimensions: int) -> list[float]:
    """Return a deterministic, L2-normalized hashed bag-of-words embedding."""
    terms = tokenize(value)
    if not terms:
        return [0.0] * dimensions
    vector = [0.0] * dimensions
    for term, count in Counter(terms).items():
        digest = hashlib.sha256(term.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign * (1.0 + math.log(count))
    norm = math.sqrt(sum(item * item for item in vector))
    if norm == 0:
        return vector
    return [item / norm for item in vector]


class HashEmbeddingProvider:
    """Offline embedding provider backed by deterministic hashing of tokens."""

    name = "hash"

    def __init__(self, dimensions: int, model: str = "hash-text-v1") -> None:
        self.dimensions = dimensions
        self.model = model

    def embed(self, value: str) -> list[float]:
        """Return the hashed embedding vector for the given text."""
        return hashed_text_embedding(value, self.dimensions)

    async def embed_async(self, value: str) -> list[float]:
        """Async wrapper around :meth:`embed`; hashing is CPU-bound and non-blocking."""
        return self.embed(value)


class OpenAICompatibleEmbeddingProvider:
    """Embedding provider that calls an OpenAI-compatible ``/v1/embeddings`` API."""

    name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        model: str,
        dimensions: int,
        timeout_seconds: float,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds
        self._async_client: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        """Return the shared async client, creating it on first use."""
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=self.timeout_seconds)
        return self._async_client

    async def aclose(self) -> None:
        """Close the shared async client; called on service shutdown."""
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None

    def _parse(self, payload: object) -> list[float]:
        embedding = payload.get("data", [{}])[0].get("embedding") if isinstance(payload, dict) else None
        if not isinstance(embedding, list):
            raise ValueError("embedding response did not contain data[0].embedding")
        vector = [float(item) for item in embedding]
        if len(vector) != self.dimensions:
            raise ValueError(f"embedding returned {len(vector)} dimensions; expected {self.dimensions}")
        return vector

    def embed(self, value: str) -> list[float]:
        """Request an embedding from the API and validate its dimensionality."""
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/v1/embeddings",
                json={"model": self.model, "input": value},
            )
            response.raise_for_status()
            payload = response.json()
        return self._parse(payload)

    async def embed_async(self, value: str) -> list[float]:
        """Request an embedding over async HTTP so the event loop is not blocked."""
        response = await self._client().post(
            f"{self.base_url}/v1/embeddings",
            json={"model": self.model, "input": value},
        )
        response.raise_for_status()
        return self._parse(response.json())


def build_embedding_provider(
    provider: str,
    dimensions: int,
    model: str,
    base_url: str,
    timeout_seconds: float,
) -> EmbeddingProvider:
    """Construct the configured embedding provider, validating required options."""
    if provider == "hash":
        return HashEmbeddingProvider(dimensions=dimensions, model=model or "hash-text-v1")
    if provider == "openai-compatible":
        if not base_url:
            raise ValueError("embedding_base_url must be set for openai-compatible embeddings")
        if not model:
            raise ValueError("embedding_model must be set for openai-compatible embeddings")
        return OpenAICompatibleEmbeddingProvider(base_url, model, dimensions, timeout_seconds)
    raise ValueError("embedding provider must be either 'hash' or 'openai-compatible'")
