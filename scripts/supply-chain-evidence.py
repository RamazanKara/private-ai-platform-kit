#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "results/supply-chain"
EXPECTED_IMAGES = {"inference-gateway", "rag-service"}


def rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def latest_summary(output_dir: Path) -> Path | None:
    candidates = sorted(output_dir.glob("supply-chain-summary-*.json"))
    if candidates:
        return max(candidates, key=lambda path: path.stat().st_mtime)
    sample = output_dir / "sample-summary.json"
    return sample if sample.exists() else None


def require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def resolve_repo_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_checksums(path: Path, errors: list[str]) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            errors.append(f"{rel(path)} has malformed checksum line: {line}")
            continue
        checksums[parts[1].strip()] = parts[0].strip()
    return checksums


def validate_sbom(path: Path, errors: list[str]) -> None:
    payload = load_json(path)
    require(errors, isinstance(payload, dict), f"{rel(path)} must be a JSON object")
    if not isinstance(payload, dict):
        return
    require(errors, str(payload.get("spdxVersion", "")).startswith("SPDX-"), f"{rel(path)} must be SPDX JSON")
    packages = payload.get("packages", [])
    require(errors, isinstance(packages, list) and bool(packages), f"{rel(path)} must include package entries")


def validate_sarif(path: Path, errors: list[str]) -> None:
    payload = load_json(path)
    require(errors, isinstance(payload, dict), f"{rel(path)} must be a JSON object")
    if not isinstance(payload, dict):
        return
    require(errors, payload.get("version") == "2.1.0", f"{rel(path)} must be SARIF 2.1.0")
    runs = payload.get("runs", [])
    require(errors, isinstance(runs, list) and bool(runs), f"{rel(path)} must include SARIF runs")
    findings = 0
    if isinstance(runs, list):
        for run in runs:
            if isinstance(run, dict):
                results = run.get("results", [])
                if isinstance(results, list):
                    findings += len(results)
                else:
                    errors.append(f"{rel(path)} run results must be a list")
    require(errors, findings == 0, f"{rel(path)} must have zero HIGH/CRITICAL findings")


def validate_summary(path: Path, strict_current: bool = False) -> list[str]:
    errors: list[str] = []
    payload = load_json(path)
    if not isinstance(payload, dict):
        return [f"{rel(path)} must be a JSON object"]

    sample = path.name.startswith("sample-")
    if strict_current and sample:
        errors.append("latest supply-chain evidence is a checked-in sample; run make image-scan")

    require(errors, payload.get("project") == "Private AI Platform Kit", f"{rel(path)} project must match")
    require(errors, payload.get("status") == "pass", f"{rel(path)} status must be pass")
    require(errors, "HIGH and CRITICAL" in str(payload.get("gate", "")), f"{rel(path)} must document the HIGH/CRITICAL gate")
    images = payload.get("images", [])
    require(errors, isinstance(images, list) and len(images) == 2, f"{rel(path)} must describe two service images")
    if not isinstance(images, list):
        return errors

    names = {str(item.get("name", "")) for item in images if isinstance(item, dict)}
    require(errors, names == EXPECTED_IMAGES, f"{rel(path)} must cover {sorted(EXPECTED_IMAGES)}")

    if sample:
        return errors

    referenced: list[Path] = []
    for item in images:
        if not isinstance(item, dict):
            errors.append(f"{rel(path)} image entries must be objects")
            continue
        sbom = resolve_repo_path(str(item.get("sbom", "")))
        sarif = resolve_repo_path(str(item.get("trivy_sarif", "")))
        require(errors, sbom.exists(), f"{rel(sbom)} must exist")
        require(errors, sarif.exists(), f"{rel(sarif)} must exist")
        if sbom.exists():
            validate_sbom(sbom, errors)
            referenced.append(sbom)
        if sarif.exists():
            validate_sarif(sarif, errors)
            referenced.append(sarif)

    checksum_path = resolve_repo_path(str(payload.get("checksums", "")))
    require(errors, checksum_path.exists(), f"{rel(checksum_path)} must exist")
    if checksum_path.exists():
        checksums = parse_checksums(checksum_path, errors)
        for artifact in referenced:
            artifact_key = rel(artifact)
            require(errors, artifact_key in checksums, f"{rel(checksum_path)} must include {artifact_key}")
            if artifact_key in checksums:
                require(errors, checksums[artifact_key] == sha256(artifact), f"{artifact_key} checksum mismatch")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate local supply-chain scan evidence.")
    parser.add_argument("--summary", help="Specific supply-chain summary JSON to validate.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory containing supply-chain evidence.")
    parser.add_argument("--strict-current", action="store_true", help="Fail when only sample evidence is available.")
    parser.add_argument("--check", action="store_true", help="Run checks and exit non-zero on failures.")
    args = parser.parse_args()

    summary = Path(args.summary) if args.summary else latest_summary(ROOT / args.output_dir)
    if summary is None:
        print("supply-chain evidence check failed:")
        print("- no supply-chain summary found")
        return 1
    summary = summary if summary.is_absolute() else ROOT / summary
    try:
        errors = validate_summary(summary, strict_current=args.strict_current)
    except (OSError, json.JSONDecodeError) as exc:
        errors = [f"failed to read {rel(summary)}: {exc}"]

    if errors:
        print("supply-chain evidence check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"supply-chain evidence ok: {rel(summary)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
