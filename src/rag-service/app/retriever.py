"""Knowledge retrievers providing lexical and Qdrant-backed grounded context."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.embeddings import EmbeddingProvider, HashEmbeddingProvider, tokenize


@dataclass(frozen=True)
class KnowledgeDocument:
    """An indexed knowledge document with its text and token frequency counts."""

    id: str
    title: str
    source: str
    content: str
    tokens: Counter[str]


@dataclass(frozen=True)
class RetrievalResult:
    """A retrieved document with its relevance score and a context excerpt."""

    document: KnowledgeDocument
    score: float
    excerpt: str


class VectorStoreError(RuntimeError):
    """Raised when the vector store cannot be reached or bootstrapped."""


def _title_from_content(path: Path, content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or path.stem
        if stripped:
            return stripped[:120]
    return path.stem


def load_documents(document_dir: Path) -> list[KnowledgeDocument]:
    """Load markdown and text files under the directory into knowledge documents."""
    if not document_dir.exists():
        return []
    documents: list[KnowledgeDocument] = []
    for path in sorted(document_dir.rglob("*")):
        relative_path = path.relative_to(document_dir)
        if any(part.startswith(".") for part in relative_path.parts):
            continue
        if not path.is_file() or path.suffix.lower() not in {".md", ".txt"}:
            continue
        content = path.read_text(encoding="utf-8")
        relative = relative_path.as_posix()
        doc_id = relative.rsplit(".", 1)[0].replace("/", "-")
        documents.append(
            KnowledgeDocument(
                id=doc_id,
                title=_title_from_content(path, content),
                source=relative,
                content=content,
                tokens=Counter(tokenize(content)),
            )
        )
    return documents


def _excerpt(content: str, terms: set[str], max_chars: int = 700) -> str:
    compact = " ".join(content.split())
    lowered = compact.lower()
    first_match = min(
        (lowered.find(term) for term in terms if lowered.find(term) >= 0),
        default=0,
    )
    start = max(first_match - 160, 0)
    end = min(start + max_chars, len(compact))
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(compact) else ""
    return f"{prefix}{compact[start:end]}{suffix}"


class LexicalRetriever:
    """In-memory retriever ranking documents by token overlap with the query."""

    def __init__(self, documents: list[KnowledgeDocument]) -> None:
        self.documents = documents

    @classmethod
    def from_directory(cls, document_dir: Path) -> LexicalRetriever:
        """Build a lexical retriever from documents loaded under a directory."""
        return cls(load_documents(document_dir))

    async def query(self, query: str, top_k: int, max_context_chars: int) -> list[RetrievalResult]:
        """Return the top-k documents scored by term, phrase, and title matches."""
        terms = tokenize(query)
        if not terms:
            return []
        unique_terms = set(terms)
        query_lower = query.lower()
        ranked: list[RetrievalResult] = []
        for document in self.documents:
            score = 0.0
            for term in unique_terms:
                score += document.tokens.get(term, 0)
            content_lower = document.content.lower()
            if query_lower and query_lower in content_lower:
                score += 5.0
            if any(term in document.title.lower() for term in unique_terms):
                score += 2.0
            if score <= 0:
                continue
            ranked.append(
                RetrievalResult(
                    document=document,
                    score=score,
                    excerpt=_excerpt(document.content, unique_terms, max_context_chars),
                )
            )
        return sorted(ranked, key=lambda result: (-result.score, result.document.id))[:top_k]


class QdrantRetriever:
    """Vector retriever that embeds queries and searches a Qdrant collection."""

    def __init__(
        self,
        documents: list[KnowledgeDocument],
        base_url: str,
        collection: str,
        collection_version: str,
        timeout_seconds: float,
        vector_dimensions: int,
        bootstrap_from_knowledge: bool,
        embedding_provider: EmbeddingProvider | None = None,
        candidate_multiplier: int = 4,
        lexical_weight: float = 0.5,
        allowed_classifications: tuple[str, ...] = (),
    ) -> None:
        self.documents = documents
        self.base_url = base_url.rstrip("/")
        self.collection = collection
        self.collection_version = collection_version
        self.timeout_seconds = timeout_seconds
        self.vector_dimensions = vector_dimensions
        self.bootstrap_from_knowledge = bootstrap_from_knowledge
        self.embedding_provider = embedding_provider or HashEmbeddingProvider(vector_dimensions)
        self.candidate_multiplier = candidate_multiplier
        self.lexical_weight = lexical_weight
        self.allowed_classifications = allowed_classifications
        self._bootstrapped = False
        self.last_sync_status = "pending" if bootstrap_from_knowledge else "disabled"
        self.last_sync_error = ""

    @classmethod
    def from_directory(
        cls,
        document_dir: Path,
        base_url: str,
        collection: str,
        collection_version: str,
        timeout_seconds: float,
        vector_dimensions: int,
        bootstrap_from_knowledge: bool,
        embedding_provider: EmbeddingProvider | None = None,
        candidate_multiplier: int = 4,
        lexical_weight: float = 0.5,
        allowed_classifications: tuple[str, ...] = (),
    ) -> QdrantRetriever:
        """Build a Qdrant retriever seeded with documents loaded from a directory."""
        return cls(
            documents=load_documents(document_dir),
            base_url=base_url,
            collection=collection,
            collection_version=collection_version,
            timeout_seconds=timeout_seconds,
            vector_dimensions=vector_dimensions,
            bootstrap_from_knowledge=bootstrap_from_knowledge,
            embedding_provider=embedding_provider,
            candidate_multiplier=candidate_multiplier,
            lexical_weight=lexical_weight,
            allowed_classifications=allowed_classifications,
        )

    def status(self) -> dict[str, str | int | bool]:
        """Return collection, embedding, and last-sync status for health checks."""
        return {
            "collection": self.collection,
            "collection_version": self.collection_version,
            "documents": len(self.documents),
            "vector_dimensions": self.vector_dimensions,
            "embedding_provider": self.embedding_provider.name,
            "embedding_model": self.embedding_provider.model,
            "bootstrap_from_knowledge": self.bootstrap_from_knowledge,
            "last_sync_status": self.last_sync_status,
            "last_sync_error": self.last_sync_error,
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout_seconds)

    async def _ensure_collection(self, client: httpx.AsyncClient) -> None:
        collection_url = f"{self.base_url}/collections/{self.collection}"
        response = await client.get(collection_url)
        if response.status_code == 404:
            response = await client.put(
                collection_url,
                json={
                    "vectors": {
                        "size": self.vector_dimensions,
                        "distance": "Cosine",
                    }
                },
            )
        response.raise_for_status()

    async def _upsert_documents(self, client: httpx.AsyncClient) -> None:
        if not self.documents:
            self.last_sync_status = "empty"
            return
        points = []
        for document in self.documents:
            points.append(
                {
                    "id": str(uuid5(NAMESPACE_URL, f"{self.collection_version}:{document.source}:{document.id}")),
                    "vector": await self.embedding_provider.embed_async(document.content),
                    "payload": {
                        "collection_version": self.collection_version,
                        "document_id": document.id,
                        "title": document.title,
                        "source": document.source,
                        "content": document.content,
                    },
                }
            )
        response = await client.put(
            f"{self.base_url}/collections/{self.collection}/points",
            params={"wait": "true"},
            json={"points": points},
        )
        response.raise_for_status()
        self.last_sync_status = "synced"
        self.last_sync_error = ""

    async def _ensure_bootstrapped(self) -> None:
        if self._bootstrapped or not self.bootstrap_from_knowledge:
            return
        try:
            async with self._client() as client:
                await self._ensure_collection(client)
                await self._upsert_documents(client)
            self._bootstrapped = True
        except httpx.HTTPError as exc:
            self.last_sync_status = "failed"
            self.last_sync_error = str(exc)
            raise VectorStoreError("qdrant bootstrap failed") from exc

    async def bootstrap(self) -> None:
        """Eagerly bootstrap the collection at startup so reachability surfaces early.

        Avoids a cold-start thundering herd where every concurrent first request
        races to create/upsert the collection. Failures propagate as VectorStoreError.
        """
        await self._ensure_bootstrapped()

    async def ping(self) -> bool:
        """Return whether the Qdrant collection endpoint is reachable for readiness."""
        try:
            async with self._client() as client:
                response = await client.get(f"{self.base_url}/collections/{self.collection}")
                response.raise_for_status()
        except httpx.HTTPError:
            return False
        return True

    def _query_filter(self) -> dict[str, Any]:
        """Build the Qdrant filter: collection version, plus a classification allowlist.

        ``allowed_classifications`` (when set) access-scopes retrieval so a caller only
        sees documents whose ``classification`` payload field is in the allowlist; empty
        returns every classification.
        """
        must: list[dict[str, Any]] = [{"key": "collection_version", "match": {"value": self.collection_version}}]
        if self.allowed_classifications:
            must.append({"key": "classification", "match": {"any": list(self.allowed_classifications)}})
        return {"must": must}

    def _hybrid_score(self, dense_score: float, query_terms: set[str], content: str) -> float:
        """Blend the dense cosine score with lexical query-term overlap.

        Lexical overlap (the fraction of query terms present in the document) complements
        the dense signal, which materially improves ranking under the default hashed-vector
        embedding. ``lexical_weight`` of 0 reproduces pure dense ranking.
        """
        dense = max(0.0, dense_score)
        if not query_terms:
            return dense
        # Substring match (not exact token-set intersection): the tokenizer keeps trailing
        # punctuation, so "gateway." would otherwise miss the query term "gateway".
        content_lower = content.lower()
        hits = sum(1 for term in query_terms if term in content_lower)
        overlap = hits / len(query_terms)
        weight = self.lexical_weight
        return (1.0 - weight) * dense + weight * overlap

    async def query(self, query: str, top_k: int, max_context_chars: int) -> list[RetrievalResult]:
        """Embed the query, fetch candidates, and return the hybrid-reranked top-k points."""
        terms = set(tokenize(query))
        if not terms:
            return []
        await self._ensure_bootstrapped()
        vector = await self.embedding_provider.embed_async(query)
        # Over-fetch dense candidates so the lexical rerank has room to reorder.
        candidate_limit = max(top_k, top_k * self.candidate_multiplier)
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self.base_url}/collections/{self.collection}/points/query",
                    json={
                        "query": vector,
                        "limit": candidate_limit,
                        "with_payload": True,
                        "filter": self._query_filter(),
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise VectorStoreError("qdrant query failed") from exc

        result = payload.get("result", {})
        if isinstance(result, dict):
            points = result.get("points", [])
        elif isinstance(result, list):
            points = result
        else:
            points = []

        matches: list[RetrievalResult] = []
        for point in points:
            if not isinstance(point, dict):
                continue
            point_payload = point.get("payload") or {}
            if not isinstance(point_payload, dict):
                point_payload = {}
            content = str(point_payload.get("content") or point_payload.get("text") or "")
            source = str(point_payload.get("source") or "qdrant")
            doc_id = str(point_payload.get("document_id") or point_payload.get("id") or point.get("id"))
            title = str(point_payload.get("title") or doc_id)
            combined = self._hybrid_score(float(point.get("score") or 0.0), terms, content)
            matches.append(
                RetrievalResult(
                    document=KnowledgeDocument(
                        id=doc_id,
                        title=title,
                        source=source,
                        content=content,
                        tokens=Counter(tokenize(content)),
                    ),
                    score=combined,
                    excerpt=_excerpt(content or title, terms, max_context_chars),
                )
            )
        matches.sort(key=lambda match: (-match.score, match.document.id))
        return matches[:top_k]


def build_context(results: list[RetrievalResult], max_context_chars: int) -> str:
    """Concatenate retrieval excerpts into a context block within the char budget."""
    sections: list[str] = []
    used = 0
    for result in results:
        header = f"[{result.document.id}] {result.document.title} ({result.document.source})"
        body = result.excerpt
        section = f"{header}\n{body}"
        remaining = max_context_chars - used
        if remaining <= 0:
            break
        if len(section) > remaining:
            section = section[:remaining].rstrip()
        sections.append(section)
        used += len(section)
    return "\n\n".join(sections)
