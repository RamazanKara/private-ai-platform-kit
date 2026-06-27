from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx
import yaml

from app.embeddings import EmbeddingProvider, build_embedding_provider


REQUIRED_SOURCE_FIELDS = {
    "id",
    "source",
    "classification",
    "retentionClass",
    "owner",
    "embeddingModel",
}
TEXT_SUFFIXES = {".md", ".markdown", ".txt"}


@dataclass(frozen=True)
class SourceRecord:
    id: str
    source: str
    classification: str
    retention_class: str
    owner: str
    embedding_model: str
    base_dir: Path

    @property
    def resolved_source(self) -> Path:
        path = Path(self.source)
        if path.is_absolute():
            return path
        return (self.base_dir / path).resolve()


@dataclass(frozen=True)
class ChunkRecord:
    source: SourceRecord
    document_path: Path
    chunk_index: int
    text: str
    collection_version: str

    @property
    def point_id(self) -> str:
        key = f"{self.collection_version}:{self.source.id}:{self.document_path.as_posix()}:{self.chunk_index}"
        return str(uuid5(NAMESPACE_URL, key))

    def payload(self) -> dict[str, Any]:
        return {
            "collection_version": self.collection_version,
            "source_id": self.source.id,
            "source": self.source.source,
            "classification": self.source.classification,
            "retentionClass": self.source.retention_class,
            "owner": self.source.owner,
            "embeddingModel": self.source.embedding_model,
            "document_path": self.document_path.as_posix(),
            "chunk_index": self.chunk_index,
            "content": self.text,
        }


def load_manifest(path: Path) -> list[SourceRecord]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("RAG source manifest must be a YAML mapping")
    if data.get("apiVersion") != "platform.ai/v1alpha1":
        raise ValueError("RAG source manifest apiVersion must be platform.ai/v1alpha1")
    if data.get("kind") != "RagSourceManifest":
        raise ValueError("RAG source manifest kind must be RagSourceManifest")
    sources = data.get("spec", {}).get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("RAG source manifest spec.sources must be a non-empty list")

    records: list[SourceRecord] = []
    seen: set[str] = set()
    for index, item in enumerate(sources):
        if not isinstance(item, dict):
            raise ValueError(f"spec.sources[{index}] must be a mapping")
        missing = sorted(REQUIRED_SOURCE_FIELDS - set(item))
        if missing:
            raise ValueError(f"spec.sources[{index}] missing fields: {missing}")
        source_id = str(item["id"]).strip()
        if not source_id:
            raise ValueError(f"spec.sources[{index}].id must not be empty")
        if source_id in seen:
            raise ValueError(f"duplicate source id: {source_id}")
        seen.add(source_id)
        records.append(
            SourceRecord(
                id=source_id,
                source=str(item["source"]).strip(),
                classification=str(item["classification"]).strip(),
                retention_class=str(item["retentionClass"]).strip(),
                owner=str(item["owner"]).strip(),
                embedding_model=str(item["embeddingModel"]).strip(),
                base_dir=path.parent,
            )
        )
    return records


def iter_document_paths(source: SourceRecord) -> list[Path]:
    root = source.resolved_source
    if root.is_file():
        return [root]
    if not root.is_dir():
        raise ValueError(f"source {source.id} path does not exist: {root}")
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.suffix.lower() in TEXT_SUFFIXES
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
    ]


def chunk_text(value: str, chunk_chars: int, overlap_chars: int) -> list[str]:
    compact = "\n".join(line.rstrip() for line in value.splitlines()).strip()
    if not compact:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(compact):
        end = min(start + chunk_chars, len(compact))
        if end < len(compact):
            boundary = compact.rfind("\n\n", start, end)
            if boundary > start + chunk_chars // 2:
                end = boundary
        chunk = compact[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(compact):
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


def build_chunks(
    sources: list[SourceRecord],
    chunk_chars: int,
    overlap_chars: int,
    collection_version: str = "v1",
) -> list[ChunkRecord]:
    chunks: list[ChunkRecord] = []
    for source in sources:
        root = source.resolved_source
        for path in iter_document_paths(source):
            relative = path.relative_to(root) if root.is_dir() else Path(path.name)
            text = path.read_text(encoding="utf-8")
            for index, chunk in enumerate(chunk_text(text, chunk_chars, overlap_chars)):
                chunks.append(
                    ChunkRecord(
                        source=source,
                        document_path=Path(relative),
                        chunk_index=index,
                        text=chunk,
                        collection_version=collection_version,
                    )
                )
    return chunks


def _collection_dimensions(payload: dict[str, Any]) -> int | None:
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        return None
    vectors = result.get("config", {}).get("params", {}).get("vectors")
    if isinstance(vectors, dict) and isinstance(vectors.get("size"), int):
        return vectors["size"]
    return None


def ensure_collection(
    client: httpx.Client,
    base_url: str,
    collection: str,
    dimensions: int,
) -> str:
    url = f"{base_url.rstrip('/')}/collections/{collection}"
    response = client.get(url)
    if response.status_code == 404:
        created = client.put(
            url,
            json={"vectors": {"size": dimensions, "distance": "Cosine"}},
        )
        created.raise_for_status()
        return "created"
    response.raise_for_status()
    existing_dimensions = _collection_dimensions(response.json())
    if existing_dimensions is not None and existing_dimensions != dimensions:
        raise ValueError(
            f"collection {collection} has {existing_dimensions} dimensions; expected {dimensions}"
        )
    return "ready"


def upsert_chunks(
    chunks: list[ChunkRecord],
    provider: EmbeddingProvider,
    base_url: str,
    collection: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    with httpx.Client(timeout=timeout_seconds) as client:
        collection_status = ensure_collection(client, base_url, collection, provider.dimensions)
        if chunks:
            response = client.put(
                f"{base_url.rstrip('/')}/collections/{collection}/points",
                params={"wait": "true"},
                json={
                    "points": [
                        {
                            "id": chunk.point_id,
                            "vector": provider.embed(chunk.text),
                            "payload": chunk.payload(),
                        }
                        for chunk in chunks
                    ]
                },
            )
            response.raise_for_status()
    return {
        "collection": collection,
        "collection_status": collection_status,
        "upserted_chunks": len(chunks),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate and ingest platform.ai/v1alpha1 RAG source manifests into Qdrant.",
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--backend", choices=("qdrant",), default="qdrant")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--qdrant-url", default="")
    parser.add_argument("--collection", default="private-ai-platform-kit")
    parser.add_argument("--collection-version", default="v1")
    parser.add_argument("--dimensions", type=int, default=384)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--embedding-provider", default="hash")
    parser.add_argument("--embedding-base-url", default="")
    parser.add_argument("--embedding-model", default="hash-text-v1")
    parser.add_argument("--chunk-chars", type=int, default=1200)
    parser.add_argument("--overlap-chars", type=int, default=120)
    args = parser.parse_args()

    if args.dimensions <= 0:
        raise SystemExit("--dimensions must be greater than zero")
    if not args.collection_version.strip():
        raise SystemExit("--collection-version must not be empty")
    if args.chunk_chars <= 0:
        raise SystemExit("--chunk-chars must be greater than zero")
    if args.overlap_chars < 0 or args.overlap_chars >= args.chunk_chars:
        raise SystemExit("--overlap-chars must be zero or less than --chunk-chars")

    sources = load_manifest(args.source)
    chunks = build_chunks(sources, args.chunk_chars, args.overlap_chars, args.collection_version)
    provider = build_embedding_provider(
        args.embedding_provider,
        args.dimensions,
        args.embedding_model,
        args.embedding_base_url,
        args.timeout_seconds,
    )
    summary: dict[str, Any] = {
        "status": "checked",
        "backend": args.backend,
        "embedding_provider": provider.name,
        "embedding_model": provider.model,
        "dimensions": provider.dimensions,
        "sources": len(sources),
        "documents": sum(len(iter_document_paths(source)) for source in sources),
        "chunks": len(chunks),
        "collection": args.collection,
        "collection_version": args.collection_version,
    }
    if args.write:
        if not args.qdrant_url:
            raise SystemExit("--qdrant-url is required with --write")
        summary.update(
            upsert_chunks(
                chunks,
                provider,
                args.qdrant_url,
                args.collection,
                args.timeout_seconds,
            )
        )
        summary["status"] = "written"
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
