"""CLI to validate RAG source manifests and ingest chunked documents into Qdrant."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
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
    """A validated RAG manifest source entry with its governance metadata."""

    id: str
    source: str
    classification: str
    retention_class: str
    owner: str
    embedding_model: str
    base_dir: Path

    @property
    def resolved_source(self) -> Path:
        """Return the source path resolved against the manifest's base directory."""
        path = Path(self.source)
        if path.is_absolute():
            return path
        return (self.base_dir / path).resolve()


@dataclass(frozen=True)
class ChunkRecord:
    """A single text chunk of a source document destined for the vector store."""

    source: SourceRecord
    document_path: Path
    chunk_index: int
    text: str
    collection_version: str

    @property
    def point_id(self) -> str:
        """Return a stable UUID5 point id derived from version, source, path, and index."""
        key = f"{self.collection_version}:{self.source.id}:{self.document_path.as_posix()}:{self.chunk_index}"
        return str(uuid5(NAMESPACE_URL, key))

    def payload(self) -> dict[str, Any]:
        """Return the Qdrant point payload with content and governance metadata."""
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
    """Load and validate a RagSourceManifest, returning its source records."""
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
    """Return the sorted text document paths contained in a source location."""
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
    """Split text into overlapping chunks, preferring paragraph boundaries."""
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
    """Read every source document and return its chunk records for ingestion."""
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
    """Create the Qdrant collection if absent and verify its vector dimensions."""
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
        raise ValueError(f"collection {collection} has {existing_dimensions} dimensions; expected {dimensions}")
    return "ready"


def upsert_chunks(
    chunks: list[ChunkRecord],
    provider: EmbeddingProvider,
    base_url: str,
    collection: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Embed and upsert chunk points into Qdrant, returning an ingestion summary."""
    now = datetime.now(UTC)
    ingested_at = now.isoformat()
    ingested_at_epoch = int(now.timestamp())

    def _point(chunk: ChunkRecord) -> dict[str, Any]:
        payload = chunk.payload()
        # Stamp ingestion time so age-based retention purge can range-filter on ingestedAtEpoch.
        payload["ingestedAt"] = ingested_at
        payload["ingestedAtEpoch"] = ingested_at_epoch
        return {"id": chunk.point_id, "vector": provider.embed(chunk.text), "payload": payload}

    with httpx.Client(timeout=timeout_seconds) as client:
        collection_status = ensure_collection(client, base_url, collection, provider.dimensions)
        if chunks:
            response = client.put(
                f"{base_url.rstrip('/')}/collections/{collection}/points",
                params={"wait": "true"},
                json={"points": [_point(chunk) for chunk in chunks]},
            )
            response.raise_for_status()
    return {
        "collection": collection,
        "collection_status": collection_status,
        "upserted_chunks": len(chunks),
    }


def delete_older_than(
    cutoff_epoch: int,
    base_url: str,
    collection: str,
    timeout_seconds: float,
    collection_version: str | None = None,
) -> dict[str, Any]:
    """Delete points ingested before ``cutoff_epoch`` (age-based retention enforcement)."""
    must: list[dict[str, Any]] = [{"key": "ingestedAtEpoch", "range": {"lt": cutoff_epoch}}]
    if collection_version:
        must.append({"key": "collection_version", "match": {"value": collection_version}})
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/collections/{collection}/points/delete",
            params={"wait": "true"},
            json={"filter": {"must": must}},
        )
        response.raise_for_status()
    return {
        "status": "purged",
        "collection": collection,
        "cutoff_epoch": cutoff_epoch,
        "collection_version": collection_version or "all",
    }


def delete_source(
    source_id: str,
    base_url: str,
    collection: str,
    timeout_seconds: float,
    collection_version: str | None = None,
) -> dict[str, Any]:
    """Delete every point for a source id from Qdrant.

    Enables right-to-erasure and source removal: dropping a source from the manifest
    otherwise orphans its vectors forever. Optionally scope the delete to one
    collection version; omit it to purge the source across all versions.
    """
    must: list[dict[str, Any]] = [{"key": "source_id", "match": {"value": source_id}}]
    if collection_version:
        must.append({"key": "collection_version", "match": {"value": collection_version}})
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/collections/{collection}/points/delete",
            params={"wait": "true"},
            json={"filter": {"must": must}},
        )
        response.raise_for_status()
    return {
        "status": "deleted",
        "collection": collection,
        "deleted_source_id": source_id,
        "collection_version": collection_version or "all",
    }


def main() -> int:
    """Parse CLI arguments, then check, write, or delete manifest chunks in Qdrant."""
    parser = argparse.ArgumentParser(
        description="Validate, ingest, or purge platform.ai/v1alpha1 RAG source manifests in Qdrant.",
    )
    parser.add_argument("--source", type=Path)
    parser.add_argument("--source-id", default="", help="Source id to purge with --delete.")
    parser.add_argument("--older-than-days", type=int, default=0, help="Age threshold for --purge.")
    parser.add_argument("--backend", choices=("qdrant",), default="qdrant")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--delete", action="store_true", help="Delete all points for --source-id from Qdrant.")
    mode.add_argument("--purge", action="store_true", help="Delete points older than --older-than-days.")
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

    if args.delete:
        if not args.source_id.strip():
            raise SystemExit("--source-id is required with --delete")
        if not args.qdrant_url:
            raise SystemExit("--qdrant-url is required with --delete")
        delete_summary = delete_source(
            args.source_id.strip(),
            args.qdrant_url,
            args.collection,
            args.timeout_seconds,
            args.collection_version,
        )
        print(json.dumps(delete_summary, indent=2, sort_keys=True))
        return 0

    if args.purge:
        if args.older_than_days <= 0:
            raise SystemExit("--older-than-days must be greater than zero with --purge")
        if not args.qdrant_url:
            raise SystemExit("--qdrant-url is required with --purge")
        cutoff_epoch = int(datetime.now(UTC).timestamp()) - args.older_than_days * 86400
        purge_summary = delete_older_than(
            cutoff_epoch,
            args.qdrant_url,
            args.collection,
            args.timeout_seconds,
            args.collection_version,
        )
        purge_summary["older_than_days"] = args.older_than_days
        print(json.dumps(purge_summary, indent=2, sort_keys=True))
        return 0

    if not args.source:
        raise SystemExit("--source is required with --check or --write")
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
