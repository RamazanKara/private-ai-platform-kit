from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.embeddings import EmbeddingProvider, HashEmbeddingProvider, tokenize


@dataclass(frozen=True)
class KnowledgeDocument:
    id: str
    title: str
    source: str
    content: str
    tokens: Counter[str]


@dataclass(frozen=True)
class RetrievalResult:
    document: KnowledgeDocument
    score: float
    excerpt: str


class VectorStoreError(RuntimeError):
    pass


def _title_from_content(path: Path, content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or path.stem
        if stripped:
            return stripped[:120]
    return path.stem


def load_documents(document_dir: Path) -> list[KnowledgeDocument]:
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
    def __init__(self, documents: list[KnowledgeDocument]) -> None:
        self.documents = documents

    @classmethod
    def from_directory(cls, document_dir: Path) -> LexicalRetriever:
        return cls(load_documents(document_dir))

    def query(self, query: str, top_k: int, max_context_chars: int) -> list[RetrievalResult]:
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
    ) -> None:
        self.documents = documents
        self.base_url = base_url.rstrip("/")
        self.collection = collection
        self.collection_version = collection_version
        self.timeout_seconds = timeout_seconds
        self.vector_dimensions = vector_dimensions
        self.bootstrap_from_knowledge = bootstrap_from_knowledge
        self.embedding_provider = embedding_provider or HashEmbeddingProvider(vector_dimensions)
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
    ) -> QdrantRetriever:
        return cls(
            documents=load_documents(document_dir),
            base_url=base_url,
            collection=collection,
            collection_version=collection_version,
            timeout_seconds=timeout_seconds,
            vector_dimensions=vector_dimensions,
            bootstrap_from_knowledge=bootstrap_from_knowledge,
            embedding_provider=embedding_provider,
        )

    def status(self) -> dict[str, str | int | bool]:
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

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout_seconds)

    def _ensure_collection(self, client: httpx.Client) -> None:
        collection_url = f"{self.base_url}/collections/{self.collection}"
        response = client.get(collection_url)
        if response.status_code == 404:
            response = client.put(
                collection_url,
                json={
                    "vectors": {
                        "size": self.vector_dimensions,
                        "distance": "Cosine",
                    }
                },
            )
        response.raise_for_status()

    def _upsert_documents(self, client: httpx.Client) -> None:
        if not self.documents:
            self.last_sync_status = "empty"
            return
        points = []
        for document in self.documents:
            points.append(
                {
                    "id": str(uuid5(NAMESPACE_URL, f"{self.collection_version}:{document.source}:{document.id}")),
                    "vector": self.embedding_provider.embed(document.content),
                    "payload": {
                        "collection_version": self.collection_version,
                        "document_id": document.id,
                        "title": document.title,
                        "source": document.source,
                        "content": document.content,
                    },
                }
            )
        response = client.put(
            f"{self.base_url}/collections/{self.collection}/points",
            params={"wait": "true"},
            json={"points": points},
        )
        response.raise_for_status()
        self.last_sync_status = "synced"
        self.last_sync_error = ""

    def _ensure_bootstrapped(self) -> None:
        if self._bootstrapped or not self.bootstrap_from_knowledge:
            return
        try:
            with self._client() as client:
                self._ensure_collection(client)
                self._upsert_documents(client)
            self._bootstrapped = True
        except httpx.HTTPError as exc:
            self.last_sync_status = "failed"
            self.last_sync_error = str(exc)
            raise VectorStoreError("qdrant bootstrap failed") from exc

    def query(self, query: str, top_k: int, max_context_chars: int) -> list[RetrievalResult]:
        if not tokenize(query):
            return []
        self._ensure_bootstrapped()
        vector = self.embedding_provider.embed(query)
        try:
            with self._client() as client:
                response = client.post(
                    f"{self.base_url}/collections/{self.collection}/points/query",
                    json={
                        "query": vector,
                        "limit": top_k,
                        "with_payload": True,
                        "filter": {
                            "must": [
                                {
                                    "key": "collection_version",
                                    "match": {"value": self.collection_version},
                                }
                            ]
                        },
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

        terms = set(tokenize(query))
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
            matches.append(
                RetrievalResult(
                    document=KnowledgeDocument(
                        id=doc_id,
                        title=title,
                        source=source,
                        content=content,
                        tokens=Counter(tokenize(content)),
                    ),
                    score=float(point.get("score") or 0.0),
                    excerpt=_excerpt(content or title, terms, max_context_chars),
                )
            )
        return matches


def build_context(results: list[RetrievalResult], max_context_chars: int) -> str:
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
