"""Test model inventories without network calls or model downloads."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "model_artifacts", Path(__file__).resolve().parents[1] / "model_artifacts.py"
)
assert SPEC is not None and SPEC.loader is not None
artifacts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(artifacts)

MODEL = "example/small-model"
REVISION = "a" * 40


def metadata() -> dict:
    return {
        "id": MODEL,
        "sha": REVISION,
        "siblings": [
            {"rfilename": "config.json", "size": 10},
            {"rfilename": "model-02.safetensors", "size": 200, "lfs": {"sha256": "2" * 64, "size": 200}},
            {"rfilename": "model-01.safetensors", "size": 100, "lfs": {"sha256": "1" * 64, "size": 100}},
        ],
    }


class ModelArtifactTests(unittest.TestCase):
    def test_manifest_is_deterministic_and_contains_all_weight_shards(self) -> None:
        source = metadata()
        manifest = artifacts.manifest_from_metadata(source, MODEL, REVISION)
        source["siblings"].reverse()
        self.assertEqual(manifest, artifacts.manifest_from_metadata(source, MODEL, REVISION))
        self.assertEqual([item["path"] for item in manifest["files"]], ["model-01.safetensors", "model-02.safetensors"])
        payload = artifacts.manifest_bytes(manifest)
        artifacts.check_manifest(payload, MODEL, REVISION, hashlib.sha256(payload).hexdigest())

    def test_mutable_revision_and_invalid_repository_are_rejected_before_network(self) -> None:
        for model, revision in (
            (MODEL, "main"),
            (MODEL, "v1"),
            ("../private", REVISION),
            ("https://example.com", REVISION),
        ):
            with (
                self.subTest(model=model, revision=revision),
                patch.object(artifacts.urllib.request, "urlopen") as request,
            ):
                with self.assertRaises(ValueError):
                    artifacts.fetch_manifest(model, revision)
                request.assert_not_called()

    def test_upstream_identity_must_match_requested_pin(self) -> None:
        for field, value in (("id", "other/model"), ("sha", "b" * 40)):
            source = metadata()
            source[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "does not match"):
                artifacts.manifest_from_metadata(source, MODEL, REVISION)

    def test_missing_checksums_and_inconsistent_sizes_fail_closed(self) -> None:
        for lfs in (None, {"size": 200}, {"sha256": "x", "size": 200}, {"sha256": "2" * 64, "size": 201}):
            source = metadata()
            source["siblings"][1]["lfs"] = lfs
            with self.subTest(lfs=lfs), self.assertRaises(ValueError):
                artifacts.manifest_from_metadata(source, MODEL, REVISION)

    def test_repository_without_safetensors_cannot_pass(self) -> None:
        source = metadata()
        source["siblings"] = source["siblings"][:1]
        with self.assertRaisesRegex(ValueError, "at least one"):
            artifacts.manifest_from_metadata(source, MODEL, REVISION)

    def test_duplicate_files_and_unsafe_paths_are_rejected(self) -> None:
        for path in (
            "model-01.safetensors",
            "../model.safetensors",
            "/model.safetensors",
            "dir\\model.safetensors",
            "./model.safetensors",
        ):
            source = metadata()
            source["siblings"][1]["rfilename"] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                artifacts.manifest_from_metadata(source, MODEL, REVISION)

    def test_manifest_tampering_is_detected(self) -> None:
        manifest = artifacts.manifest_from_metadata(metadata(), MODEL, REVISION)
        digest = hashlib.sha256(artifacts.manifest_bytes(manifest)).hexdigest()
        for field, value in (("sha256", "3" * 64), ("size", 201), ("path", "other.safetensors")):
            changed = copy.deepcopy(manifest)
            changed["files"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "checksum"):
                artifacts.check_manifest(artifacts.manifest_bytes(changed), MODEL, REVISION, digest)

    def test_valid_checksum_does_not_override_wrong_model_identity(self) -> None:
        payload = artifacts.manifest_bytes(artifacts.manifest_from_metadata(metadata(), MODEL, REVISION))
        with self.assertRaisesRegex(ValueError, "identity"):
            artifacts.check_manifest(payload, "other/model", REVISION, hashlib.sha256(payload).hexdigest())

    def test_fetch_uses_pinned_metadata_endpoint_without_downloading_weights(self) -> None:
        response = io.BytesIO(json.dumps(metadata()).encode())
        with patch.object(artifacts.urllib.request, "urlopen", return_value=response) as request:
            payload = artifacts.fetch_manifest(MODEL, REVISION)
        request.assert_called_once()
        self.assertEqual(
            request.call_args.args[0].full_url,
            f"https://huggingface.co/api/models/{MODEL}/revision/{REVISION}?blobs=true",
        )
        self.assertEqual(len(json.loads(payload)["files"]), 2)


if __name__ == "__main__":
    unittest.main()
