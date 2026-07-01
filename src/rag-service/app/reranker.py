"""Cross-encoder reranker providers: a no-op default and an OpenAI/TEI-compatible HTTP backend.

The reranker is an optional second retrieval stage. Hybrid (dense + lexical) retrieval is a
recall-oriented first stage; a cross-encoder reranker reorders the over-fetched candidates by
joint query-document relevance, which raises precision exactly where dense retrieval is weakest
(paraphrase, synonymy, multi-hop). Mirrors the EmbeddingProvider abstraction so a customer can
plug a Cohere/Jina/TEI-compatible ``/rerank`` endpoint without changing the retriever.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx


class RerankerProvider(Protocol):
    """Protocol for rerankers that score candidate documents against a query."""

    name: str
    model: str

    async def rerank_async(self, query: str, documents: list[str]) -> list[float]: ...


class NoopReranker:
    """Default reranker that performs no reranking (the first-stage order is kept)."""

    name = "none"
    model = ""

    async def rerank_async(self, query: str, documents: list[str]) -> list[float]:
        """Return a zero score per document; the caller keeps the hybrid ranking."""
        return [0.0] * len(documents)


class OpenAICompatibleReranker:
    """Reranker that calls a Cohere/Jina/TEI-compatible ``/rerank`` endpoint.

    Sends ``{"model", "query", "documents"}`` and reads ``results[].{index, relevance_score}``,
    returning scores aligned to the input document order so the retriever can reorder candidates.
    """

    name = "openai-compatible"

    def __init__(self, base_url: str, model: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    def _parse(self, payload: Any, count: int) -> list[float]:
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise ValueError("rerank response did not contain a results list")
        scores = [0.0] * count
        for item in results:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            score = item.get("relevance_score", item.get("score"))
            if isinstance(index, int) and 0 <= index < count and isinstance(score, (int, float)):
                scores[index] = float(score)
        return scores

    async def rerank_async(self, query: str, documents: list[str]) -> list[float]:
        """Request relevance scores for the documents, aligned to input order."""
        if not documents:
            return []
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/rerank",
                json={"model": self.model, "query": query, "documents": documents},
            )
            response.raise_for_status()
            payload = response.json()
        return self._parse(payload, len(documents))


def build_reranker_provider(provider: str, base_url: str, model: str, timeout_seconds: float) -> RerankerProvider:
    """Construct the configured reranker provider, validating required options."""
    if provider == "none":
        return NoopReranker()
    if provider == "openai-compatible":
        if not base_url:
            raise ValueError("reranker_base_url must be set for openai-compatible reranking")
        if not model:
            raise ValueError("reranker_model must be set for openai-compatible reranking")
        return OpenAICompatibleReranker(base_url, model, timeout_seconds)
    raise ValueError("reranker provider must be either 'none' or 'openai-compatible'")
