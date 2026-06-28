from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Protocol

import httpx

TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.:/-]*")


class EmbeddingProvider(Protocol):
    name: str
    model: str
    dimensions: int

    def embed(self, value: str) -> list[float]: ...


def tokenize(value: str) -> list[str]:
    return TOKEN_PATTERN.findall(value.lower())


def hashed_text_embedding(value: str, dimensions: int) -> list[float]:
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
    name = "hash"

    def __init__(self, dimensions: int, model: str = "hash-text-v1") -> None:
        self.dimensions = dimensions
        self.model = model

    def embed(self, value: str) -> list[float]:
        return hashed_text_embedding(value, self.dimensions)


class OpenAICompatibleEmbeddingProvider:
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

    def embed(self, value: str) -> list[float]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/v1/embeddings",
                json={"model": self.model, "input": value},
            )
            response.raise_for_status()
            payload = response.json()
        embedding = payload.get("data", [{}])[0].get("embedding") if isinstance(payload, dict) else None
        if not isinstance(embedding, list):
            raise ValueError("embedding response did not contain data[0].embedding")
        vector = [float(item) for item in embedding]
        if len(vector) != self.dimensions:
            raise ValueError(f"embedding returned {len(vector)} dimensions; expected {self.dimensions}")
        return vector


def build_embedding_provider(
    provider: str,
    dimensions: int,
    model: str,
    base_url: str,
    timeout_seconds: float,
) -> EmbeddingProvider:
    if provider == "hash":
        return HashEmbeddingProvider(dimensions=dimensions, model=model or "hash-text-v1")
    if provider == "openai-compatible":
        if not base_url:
            raise ValueError("embedding_base_url must be set for openai-compatible embeddings")
        if not model:
            raise ValueError("embedding_model must be set for openai-compatible embeddings")
        return OpenAICompatibleEmbeddingProvider(base_url, model, dimensions, timeout_seconds)
    raise ValueError("embedding provider must be either 'hash' or 'openai-compatible'")
