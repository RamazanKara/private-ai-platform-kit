#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "governance/model-provenance.yaml"
HEX_SHA256 = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class ProvenanceReport:
    generated_at: str
    policy: str
    artifacts_checked: list[str]
    approved_models_checked: list[str]
    errors: list[str]


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def nested(mapping: Any, *keys: str, default: Any = None) -> Any:
    current = mapping
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return current if current is not None else default


def require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def approved_catalog_models(errors: list[str]) -> dict[str, dict[str, Any]]:
    catalog_path = ROOT / "model-catalog/models.yaml"
    require(errors, catalog_path.exists(), "model catalog must exist at model-catalog/models.yaml")
    if not catalog_path.exists():
        return {}
    catalog = load_yaml(catalog_path)
    models = nested(catalog, "spec", "models", default=[])
    require(errors, isinstance(models, list), "model catalog spec.models must be a list")
    approved: dict[str, dict[str, Any]] = {}
    for model in models if isinstance(models, list) else []:
        if isinstance(model, dict) and model.get("status") == "approved":
            approved[str(model.get("id"))] = model
    require(errors, bool(approved), "model catalog must contain approved models")
    return approved


def check_policy_shape(policy: dict[str, Any], errors: list[str]) -> list[dict[str, Any]]:
    require(errors, policy.get("apiVersion") == "platform.ai/v1alpha1", "model provenance apiVersion must be platform.ai/v1alpha1")
    require(errors, policy.get("kind") == "ModelProvenanceSet", "model provenance kind must be ModelProvenanceSet")
    required = nested(policy, "spec", "requiredEvidence", default=[])
    expected = {"sourceUri", "immutableRef", "digest", "license", "dataClassification", "riskTier", "promotionRequest", "servingProfiles"}
    require(errors, isinstance(required, list), "model provenance spec.requiredEvidence must be a list")
    require(errors, expected <= set(required or []), f"model provenance missing requiredEvidence entries: {sorted(expected - set(required or []))}")
    artifacts = nested(policy, "spec", "artifacts", default=[])
    require(errors, isinstance(artifacts, list) and bool(artifacts), "model provenance spec.artifacts must be a non-empty list")
    if not isinstance(artifacts, list):
        return []
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            errors.append("model provenance artifact entries must be mappings")
            continue
        model_id = str(artifact.get("modelId", ""))
        require(errors, bool(model_id), "model provenance artifact must define modelId")
        if model_id in seen:
            errors.append(f"duplicate model provenance artifact for {model_id}")
        seen.add(model_id)
        for key in ("status", "source", "sourceUri", "immutableRef", "license", "dataClassification", "riskTier", "promotionRequest", "catalogRef"):
            require(errors, bool(artifact.get(key)), f"{model_id}: provenance must define {key}")
        digest = artifact.get("digest", {})
        require(errors, isinstance(digest, dict), f"{model_id}: digest must be a mapping")
        require(errors, nested(digest, "algorithm") == "sha256", f"{model_id}: digest.algorithm must be sha256")
        digest_value = str(nested(digest, "value", default=""))
        require(errors, bool(HEX_SHA256.match(digest_value)), f"{model_id}: digest.value must be a 64-character lowercase sha256 hex string")
        require(errors, f"sha256:{digest_value}" in str(artifact.get("immutableRef", "")), f"{model_id}: immutableRef must include digest value")
        require(errors, nested(digest, "scope") in {"source-reference", "model-artifact"}, f"{model_id}: digest.scope must be source-reference or model-artifact")
        require(errors, bool(nested(digest, "verificationMode")), f"{model_id}: digest.verificationMode is required")
        require(errors, bool(nested(digest, "verificationCommand")), f"{model_id}: digest.verificationCommand is required")
        serving_profiles = artifact.get("servingProfiles")
        require(errors, isinstance(serving_profiles, list) and bool(serving_profiles), f"{model_id}: servingProfiles must be a non-empty list")
        evidence_refs = artifact.get("evidenceRefs")
        require(errors, isinstance(evidence_refs, dict) and bool(evidence_refs), f"{model_id}: evidenceRefs must be a non-empty mapping")
    return [artifact for artifact in artifacts if isinstance(artifact, dict)]


def validate_artifact_against_catalog(artifact: dict[str, Any], catalog: dict[str, dict[str, Any]], errors: list[str]) -> None:
    model_id = str(artifact.get("modelId"))
    model = catalog.get(model_id)
    require(errors, model is not None, f"{model_id}: provenance artifact must match an approved catalog model")
    if not model:
        return
    for field in ("status", "license", "dataClassification", "riskTier", "promotionRequest"):
        require(errors, artifact.get(field) == model.get(field), f"{model_id}: provenance {field} must match model catalog")
    require(errors, artifact.get("source") == model.get("source"), f"{model_id}: provenance source must match model catalog")
    catalog_ref = artifact.get("catalogRef")
    require(errors, catalog_ref == "model-catalog/models.yaml", f"{model_id}: catalogRef must be model-catalog/models.yaml")
    require(errors, (ROOT / str(artifact.get("promotionRequest"))).exists(), f"{model_id}: promotionRequest must exist")


def values_references_model(path: Path, model_id: str) -> bool:
    values = load_yaml(path)
    allowed = nested(values, "runtime", "allowedModels", default=[])
    deployed = nested(values, "model", "name")
    return model_id == deployed or (isinstance(allowed, list) and model_id in allowed)


def validate_refs(artifact: dict[str, Any], errors: list[str]) -> None:
    model_id = str(artifact.get("modelId"))
    for key, path_text in (artifact.get("evidenceRefs") or {}).items():
        path = ROOT / str(path_text)
        require(errors, path.exists(), f"{model_id}: evidenceRefs.{key} path does not exist: {path_text}")
    for path_text in artifact.get("servingProfiles", []) if isinstance(artifact.get("servingProfiles"), list) else []:
        path = ROOT / str(path_text)
        require(errors, path.exists(), f"{model_id}: serving profile does not exist: {path_text}")
        if path.exists() and path.suffix in {".yaml", ".yml"}:
            require(errors, values_references_model(path, model_id), f"{model_id}: serving profile {path_text} must reference the model")


def run_check(policy_path: Path) -> ProvenanceReport:
    errors: list[str] = []
    policy = load_yaml(policy_path)
    catalog = approved_catalog_models(errors)
    artifacts = check_policy_shape(policy, errors)
    by_model = {str(artifact.get("modelId")): artifact for artifact in artifacts if artifact.get("modelId")}
    for model_id in catalog:
        require(errors, model_id in by_model, f"{model_id}: approved model missing provenance artifact")
    for artifact in artifacts:
        validate_artifact_against_catalog(artifact, catalog, errors)
        validate_refs(artifact, errors)
    return ProvenanceReport(
        generated_at=datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        policy=rel(policy_path),
        artifacts_checked=sorted(by_model),
        approved_models_checked=sorted(catalog),
        errors=errors,
    )


def write_json(path: Path, report: ProvenanceReport) -> None:
    path.write_text(json.dumps(asdict(report), indent=2, sort_keys=True) + "\n")


def write_markdown(path: Path, report: ProvenanceReport) -> None:
    lines = [
        "# Model Provenance Report",
        "",
        f"Generated: `{report.generated_at}`",
        f"Policy: `{report.policy}`",
        "",
        f"Summary: {len(report.artifacts_checked)} artifacts checked, {len(report.approved_models_checked)} approved models checked, {len(report.errors)} errors.",
        "",
        "| Model | Status |",
        "| --- | --- |",
    ]
    for model_id in report.artifacts_checked:
        lines.append(f"| `{model_id}` | {'fail' if report.errors else 'pass'} |")
    if report.errors:
        lines.extend(["", "## Errors", ""])
        for error in report.errors:
            lines.append(f"- {error}")
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate approved model artifact provenance.")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--output-dir", default="results/model-provenance")
    args = parser.parse_args()

    policy_path = Path(args.policy)
    if not policy_path.is_absolute():
        policy_path = ROOT / policy_path
    report = run_check(policy_path)

    if args.report:
        output_dir = ROOT / args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        json_path = output_dir / f"model-provenance-{stamp}.json"
        md_path = output_dir / f"model-provenance-{stamp}.md"
        write_json(json_path, report)
        write_markdown(md_path, report)
        print(f"wrote {rel(json_path)} and {rel(md_path)}")

    if report.errors:
        print("model provenance check failed:")
        for error in report.errors:
            print(f"- {error}")
        return 1
    print(f"model provenance OK: {len(report.artifacts_checked)} artifact(s) checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
