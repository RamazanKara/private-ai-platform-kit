#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
RAG_SERVICE_DIR = ROOT / "src/rag-service"
sys.path.insert(0, str(RAG_SERVICE_DIR))

from app.embeddings import EmbeddingProvider, build_embedding_provider  # noqa: E402

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


def _env_first(names: tuple[str, ...], default: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None:
            return value
    return default


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
        paths = [root]
    elif root.is_dir():
        paths = [
            path
            for path in sorted(root.rglob("*"))
            if path.is_file()
            and path.suffix.lower() in TEXT_SUFFIXES
            and not any(part.startswith(".") for part in path.relative_to(root).parts)
        ]
    else:
        raise ValueError(f"source {source.id} path does not exist: {root}")
    return paths


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
            relative = path.relative_to(root) if root.is_dir() else path.name
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
    if isinstance(vectors, dict):
        size = vectors.get("size")
        if isinstance(size, int):
            return size
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
        raise ValueError(f"collection {collection} has {existing_dimensions} dimensions; expected {dimensions}")
    return "ready"


def upsert_chunks(
    chunks: list[ChunkRecord],
    provider: EmbeddingProvider,
    base_url: str,
    collection: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    ingested_at = now.isoformat()
    ingested_at_epoch = int(now.timestamp())
    with httpx.Client(timeout=timeout_seconds) as client:
        collection_status = ensure_collection(client, base_url, collection, provider.dimensions)
        points = [
            {
                "id": chunk.point_id,
                "vector": provider.embed(chunk.text),
                "payload": {
                    **chunk.payload(),
                    "ingestedAt": ingested_at,
                    "ingestedAtEpoch": ingested_at_epoch,
                },
            }
            for chunk in chunks
        ]
        if points:
            response = client.put(
                f"{base_url.rstrip('/')}/collections/{collection}/points",
                params={"wait": "true"},
                json={"points": points},
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
    """Delete points ingested before cutoff_epoch (age-based retention enforcement)."""
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
    """Delete every point for a source id from Qdrant (right-to-erasure / source removal)."""
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
    parser = argparse.ArgumentParser(
        description="Validate, ingest, or purge platform.ai/v1alpha1 RAG source manifests in Qdrant.",
    )
    parser.add_argument("--source", type=Path, help="RagSourceManifest YAML path.")
    parser.add_argument("--source-id", default="", help="Source id to purge with --delete.")
    parser.add_argument("--backend", choices=("qdrant",), default="qdrant")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Validate and summarize without writing.")
    mode.add_argument("--write", action="store_true", help="Write chunks into Qdrant.")
    mode.add_argument("--delete", action="store_true", help="Delete all points for --source-id from Qdrant.")
    mode.add_argument("--purge", action="store_true", help="Delete points older than --older-than-days.")
    parser.add_argument("--older-than-days", type=int, default=0, help="Age threshold for --purge.")
    parser.add_argument("--qdrant-url", default=_env_first(("QDRANT_URL", "VECTOR_STORE_URL"), ""))
    parser.add_argument(
        "--collection", default=_env_first(("QDRANT_COLLECTION", "VECTOR_COLLECTION"), "private-ai-platform-kit")
    )
    parser.add_argument(
        "--collection-version", default=_env_first(("QDRANT_COLLECTION_VERSION", "VECTOR_COLLECTION_VERSION"), "v1")
    )
    parser.add_argument("--dimensions", type=int, default=int(os.getenv("QDRANT_VECTOR_DIMENSIONS", "384")))
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(_env_first(("QDRANT_TIMEOUT_SECONDS", "VECTOR_TIMEOUT_SECONDS"), "5")),
    )
    parser.add_argument("--embedding-provider", default=os.getenv("RAG_EMBEDDING_PROVIDER", "hash"))
    parser.add_argument("--embedding-base-url", default=os.getenv("RAG_EMBEDDING_BASE_URL", ""))
    parser.add_argument("--embedding-model", default=os.getenv("RAG_EMBEDDING_MODEL", "hash-text-v1"))
    parser.add_argument("--chunk-chars", type=int, default=1200)
    parser.add_argument("--overlap-chars", type=int, default=120)
    parser.add_argument("--status-file", type=Path, help="Optional JSON status output path.")
    args = parser.parse_args()

    if args.dimensions <= 0:
        raise SystemExit("--dimensions must be greater than zero")
    if not args.collection_version.strip():
        raise SystemExit("--collection-version must not be empty")

    if args.delete:
        if not args.source_id.strip():
            raise SystemExit("--source-id is required with --delete")
        if not args.qdrant_url:
            raise SystemExit("--qdrant-url or QDRANT_URL is required with --delete")
        summary = delete_source(
            args.source_id.strip(),
            args.qdrant_url,
            args.collection,
            args.timeout_seconds,
            args.collection_version,
        )
        rendered = json.dumps(summary, indent=2, sort_keys=True) + "\n"
        print(rendered, end="")
        if args.status_file:
            args.status_file.parent.mkdir(parents=True, exist_ok=True)
            args.status_file.write_text(rendered, encoding="utf-8")
        return 0

    if args.purge:
        if args.older_than_days <= 0:
            raise SystemExit("--older-than-days must be greater than zero with --purge")
        if not args.qdrant_url:
            raise SystemExit("--qdrant-url or QDRANT_URL is required with --purge")
        cutoff_epoch = int(datetime.now(UTC).timestamp()) - args.older_than_days * 86400
        purge_summary = delete_older_than(
            cutoff_epoch, args.qdrant_url, args.collection, args.timeout_seconds, args.collection_version
        )
        purge_summary["older_than_days"] = args.older_than_days
        rendered = json.dumps(purge_summary, indent=2, sort_keys=True) + "\n"
        print(rendered, end="")
        if args.status_file:
            args.status_file.parent.mkdir(parents=True, exist_ok=True)
            args.status_file.write_text(rendered, encoding="utf-8")
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
        "manifest": str(args.source),
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
            raise SystemExit("--qdrant-url or QDRANT_URL is required with --write")
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

    rendered = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    if args.status_file:
        args.status_file.parent.mkdir(parents=True, exist_ok=True)
        args.status_file.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
