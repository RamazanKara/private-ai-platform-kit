#!/usr/bin/env python3
"""Package release charts with first-party image digests bound into defaults."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIRST_PARTY = {
    "inference-gateway": "gateway_digest",
    "rag-service": "rag_digest",
}


def _digest(value: str) -> str:
    if not value.startswith("sha256:") or len(value) != 71:
        raise argparse.ArgumentTypeError("digest must be sha256:<64 lowercase hex characters>")
    try:
        int(value[7:], 16)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("digest must be sha256:<64 lowercase hex characters>") from exc
    return value.lower()


def _write_yaml(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_charts(args: argparse.Namespace) -> dict:
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    for old in output.glob("*.tgz"):
        old.unlink()

    with tempfile.TemporaryDirectory(prefix="private-ai-platform-kit-charts-") as temp:
        charts = Path(temp) / "charts"
        shutil.copytree(ROOT / "deploy/charts", charts, ignore=shutil.ignore_patterns("charts"))

        for chart_name, digest_arg in FIRST_PARTY.items():
            values_path = charts / chart_name / "values.yaml"
            values = yaml.safe_load(values_path.read_text(encoding="utf-8"))
            values["image"]["digest"] = getattr(args, digest_arg)
            values["image"]["tag"] = f"v{args.version}"
            _write_yaml(values_path, values)

        # The umbrella must vendor the digest-bound component copies, not stale
        # ignored archives from a developer checkout.
        subprocess.run(["helm", "dependency", "update", str(charts / "platform")], check=True)

        packages: list[dict[str, str]] = []
        for chart in sorted(charts.iterdir(), key=lambda path: (path.name == "platform", path.name)):
            chart_yaml = chart / "Chart.yaml"
            if not chart_yaml.exists():
                continue
            metadata = yaml.safe_load(chart_yaml.read_text(encoding="utf-8"))
            if str(metadata.get("version")) != args.version:
                raise ValueError(
                    f"{chart.name} chart version {metadata.get('version')} does not match release {args.version}"
                )
            subprocess.run(["helm", "package", str(chart), "--destination", str(output)], check=True)
            archive = output / f"{metadata['name']}-{metadata['version']}.tgz"
            packages.append({"chart": metadata["name"], "archive": archive.name, "sha256": _sha256(archive)})

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_revision": args.source_revision,
        "version": args.version,
        "images": {
            "inference-gateway": args.gateway_digest,
            "rag-service": args.rag_digest,
        },
        "charts": packages,
    }
    manifest_path = output / "chart-release-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--gateway-digest", required=True, type=_digest)
    parser.add_argument("--rag-digest", required=True, type=_digest)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--output-dir", default="chart-packages")
    args = parser.parse_args()
    package_charts(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
