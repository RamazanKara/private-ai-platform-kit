#!/usr/bin/env python3
"""Reproducible inventories of Hugging Face safetensors at an immutable revision.

The manifest records upstream weight checksums without downloading model weights.
Its digest identifies the inventory, not the bytes of a locally installed model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

COMMIT = re.compile(r"[a-f0-9]{40}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def validate_identity(model_id: str, revision: str) -> None:
    if not MODEL_ID.fullmatch(model_id):
        raise ValueError("model id must be an explicit Hugging Face owner/repository")
    if not COMMIT.fullmatch(revision):
        raise ValueError("revision must be a full lowercase 40-character commit SHA")


def validate_manifest(manifest: Any, model_id: str, revision: str) -> None:
    validate_identity(model_id, revision)
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        raise ValueError("weight manifest must use schemaVersion 1")
    if manifest.get("modelId") != model_id or manifest.get("revision") != revision:
        raise ValueError("weight manifest identity must match the model and pinned revision")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("weight manifest must contain at least one safetensors file")
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("weight manifest files must be mappings")
        name = item.get("path", "")
        if not isinstance(name, str) or not name.endswith(".safetensors"):
            raise ValueError("weight manifest paths must name safetensors files")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or path.as_posix() != name:
            raise ValueError("weight manifest paths must be normalized relative paths")
        if name in seen:
            raise ValueError(f"duplicate weight file: {name}")
        seen.add(name)
        if not isinstance(item.get("sha256"), str) or not SHA256.fullmatch(item["sha256"]):
            raise ValueError(f"{name}: missing or invalid SHA-256 checksum")
        size = item.get("size")
        if type(size) is not int or size <= 0:
            raise ValueError(f"{name}: size must be a positive integer")


def manifest_from_metadata(metadata: Any, model_id: str, revision: str) -> dict[str, Any]:
    """Extract all safetensors files, rejecting incomplete or mismatched metadata."""
    validate_identity(model_id, revision)
    if not isinstance(metadata, dict) or metadata.get("id") != model_id or metadata.get("sha") != revision:
        raise ValueError("upstream model identity or resolved revision does not match the requested pin")
    siblings = metadata.get("siblings")
    if not isinstance(siblings, list):
        raise ValueError("upstream metadata must list repository files")
    files = []
    for sibling in siblings:
        if not isinstance(sibling, dict) or not isinstance(sibling.get("rfilename"), str):
            raise ValueError("invalid upstream file entry")
        name = sibling["rfilename"]
        if not name.endswith(".safetensors"):
            continue
        lfs = sibling.get("lfs")
        if not isinstance(lfs, dict) or lfs.get("size") != sibling.get("size"):
            raise ValueError(f"{name}: missing or inconsistent upstream LFS metadata")
        files.append({"path": name, "sha256": lfs.get("sha256"), "size": lfs.get("size")})
    manifest = {
        "schemaVersion": 1,
        "modelId": model_id,
        "revision": revision,
        "files": sorted(files, key=lambda item: item["path"]),
    }
    validate_manifest(manifest, model_id, revision)
    return manifest


def manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")


def fetch_manifest(model_id: str, revision: str) -> bytes:
    validate_identity(model_id, revision)
    url = f"https://huggingface.co/api/models/{model_id}/revision/{revision}?blobs=true"
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "private-ai-platform-kit"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        metadata = json.load(response)
    return manifest_bytes(manifest_from_metadata(metadata, model_id, revision))


def check_manifest(payload: bytes, model_id: str, revision: str, digest: str) -> None:
    """Bind a local manifest to its declared identity and checksum."""
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("weight manifest checksum does not match provenance")
    validate_manifest(json.loads(payload), model_id, revision)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Hugging Face owner/repository")
    parser.add_argument("--revision", required=True, help="full immutable model commit SHA")
    parser.add_argument("--output", required=True, type=Path, help="manifest file to write for review")
    args = parser.parse_args()
    payload = fetch_manifest(args.model, args.revision)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    print(f"wrote {args.output}: sha256:{hashlib.sha256(payload).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
