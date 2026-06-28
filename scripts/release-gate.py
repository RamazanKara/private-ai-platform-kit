#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "slo/release-gates.yaml"
SUPPLY_CHAIN_VALIDATOR = ROOT / "scripts/supply-chain-evidence.py"


@dataclass(frozen=True)
class GateResult:
    name: str
    status: str
    summary: str
    evidence: str
    failures: list[str]


def rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def nested(mapping: Any, *keys: str, default: Any = None) -> Any:
    current = mapping
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return current if current is not None else default


def validate_config(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if config.get("apiVersion") != "platform.ai/v1alpha1":
        errors.append("release gate apiVersion must be platform.ai/v1alpha1")
    if config.get("kind") != "ReleaseGate":
        errors.append("release gate kind must be ReleaseGate")
    gates = nested(config, "spec", "gates", default={})
    if not isinstance(gates, dict) or not gates:
        errors.append("spec.gates must be a non-empty mapping")
        return errors
    expected = {"eval", "load", "restore", "toolchain", "egress", "retention", "slo", "quota", "modelProvenance", "supplyChain", "evidencePack"}
    missing = sorted(expected - set(gates))
    if missing:
        errors.append(f"spec.gates missing required gates: {missing}")
    for name, gate in gates.items():
        if not isinstance(gate, dict):
            errors.append(f"gate {name} must be a mapping")
            continue
        evidence = gate.get("evidence")
        if not isinstance(evidence, list) or not all(isinstance(item, str) and item for item in evidence):
            errors.append(f"gate {name}.evidence must be a non-empty string list")
    return errors


def latest_artifact(patterns: list[str]) -> Path | None:
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(path for path in ROOT.glob(pattern) if path.is_file())
    if not candidates:
        return None
    non_sample = [path for path in candidates if not path.name.startswith("sample-")]
    return max(non_sample or candidates, key=lambda path: path.stat().st_mtime)


def fail_result(name: str, evidence: str, failures: list[str]) -> GateResult:
    return GateResult(name=name, status="fail", summary="; ".join(failures), evidence=evidence, failures=failures)


def pass_result(name: str, evidence: Path, summary: str) -> GateResult:
    return GateResult(name=name, status="pass", summary=summary, evidence=rel(evidence), failures=[])


def _sample_artifact(path: Path) -> bool:
    return path.name.startswith("sample-")


def _artifact_policy_failure(name: str, artifact: Path, gate: dict[str, Any]) -> GateResult | None:
    failures: list[str] = []
    if gate.get("_require_current_evidence") and _sample_artifact(artifact):
        failures.append(
            "selected evidence is a checked-in sample artifact; rerun the gate input and use current evidence"
        )

    max_age_hours = gate.get("_max_evidence_age_hours")
    if max_age_hours is not None:
        max_age = float(max_age_hours)
        generated_at = datetime.fromtimestamp(artifact.stat().st_mtime, UTC)
        age_hours = (datetime.now(UTC) - generated_at).total_seconds() / 3600
        if age_hours > max_age:
            failures.append(f"selected evidence is {age_hours:.1f}h old; limit is {max_age:.1f}h")

    if failures:
        return fail_result(name, rel(artifact), failures)
    return None


def artifact_for(name: str, gate: dict[str, Any]) -> tuple[Path | None, GateResult | None]:
    artifact = latest_artifact(list(gate.get("evidence", [])))
    if artifact is None:
        if gate.get("required", True):
            return None, fail_result(name, "", ["missing required evidence artifact"])
        return None, GateResult(name=name, status="skip", summary="gate not required and no evidence found", evidence="", failures=[])
    policy_failure = _artifact_policy_failure(name, artifact, gate)
    if policy_failure is not None:
        return artifact, policy_failure
    return artifact, None


def check_eval(gate: dict[str, Any]) -> GateResult:
    artifact, early = artifact_for("eval", gate)
    if early:
        return early
    assert artifact is not None
    payload = load_json(artifact)
    results = payload.get("results", [])
    failures: list[str] = []
    if not isinstance(results, list):
        return fail_result("eval", rel(artifact), ["eval evidence results must be a list"])
    min_cases = int(gate.get("minCases", 1))
    max_latency = float(gate.get("maxCaseLatencyMs", 30000))
    passed = sum(1 for item in results if isinstance(item, dict) and item.get("passed") is True)
    total = len(results)
    pass_rate = passed / total if total else 0
    if total < min_cases:
        failures.append(f"eval cases {total} below minCases {min_cases}")
    if pass_rate < float(gate.get("minPassRate", 1.0)):
        failures.append(f"eval pass rate {pass_rate:.2f} below required {float(gate.get('minPassRate', 1.0)):.2f}")
    for item in results:
        if isinstance(item, dict) and float(item.get("latency_ms", 0)) > max_latency:
            failures.append(f"eval case {item.get('case_id')} latency exceeded {max_latency}ms")
    if failures:
        return fail_result("eval", rel(artifact), failures)
    return pass_result("eval", artifact, f"{passed}/{total} eval cases passed")


def metric(payload: dict[str, Any], name: str, key: str, default: float = 0.0) -> float:
    metric_payload = nested(payload, "metrics", name, default={})
    value = metric_payload.get(key, default) if isinstance(metric_payload, dict) else default
    if value == default and key == "rate" and isinstance(metric_payload, dict):
        value = metric_payload.get("value", default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def has_metric(payload: dict[str, Any], name: str, key: str) -> bool:
    metric_payload = nested(payload, "metrics", name, default={})
    if not isinstance(metric_payload, dict):
        return False
    return key in metric_payload or (key == "rate" and "value" in metric_payload)


def check_load(gate: dict[str, Any]) -> GateResult:
    artifact, early = artifact_for("load", gate)
    if early:
        return early
    assert artifact is not None
    payload = load_json(artifact)
    requests = metric(payload, "http_reqs", "count")
    error_rate = metric(payload, "http_req_failed", "rate")
    p95 = metric(payload, "http_req_duration", "p(95)")
    p99 = metric(payload, "http_req_duration", "p(99)")
    failures: list[str] = []
    for metric_name, key in (
        ("http_reqs", "count"),
        ("http_req_failed", "rate"),
        ("http_req_duration", "p(95)"),
        ("http_req_duration", "p(99)"),
    ):
        if not has_metric(payload, metric_name, key):
            failures.append(f"load evidence missing metric {metric_name}.{key}")
    if requests < float(gate.get("minRequests", 1)):
        failures.append(f"load requests {requests:g} below minRequests {float(gate.get('minRequests', 1)):g}")
    if error_rate > float(gate.get("maxErrorRate", 0.05)):
        failures.append(f"load error rate {error_rate:.4f} exceeds {float(gate.get('maxErrorRate', 0.05)):.4f}")
    if p95 > float(gate.get("maxP95LatencyMs", 10000)):
        failures.append(f"load p95 {p95:.2f}ms exceeds {float(gate.get('maxP95LatencyMs', 10000)):.2f}ms")
    if p99 > float(gate.get("maxP99LatencyMs", 15000)):
        failures.append(f"load p99 {p99:.2f}ms exceeds {float(gate.get('maxP99LatencyMs', 15000)):.2f}ms")
    if failures:
        return fail_result("load", rel(artifact), failures)
    return pass_result("load", artifact, f"{requests:g} requests, p95 {p95:.2f}ms, error rate {error_rate:.4f}")


def check_restore(gate: dict[str, Any]) -> GateResult:
    artifact, early = artifact_for("restore", gate)
    if early:
        return early
    assert artifact is not None
    payload = load_json(artifact)
    drills = payload if isinstance(payload, list) else payload.get("drills", [])
    if not isinstance(drills, list):
        return fail_result("restore", rel(artifact), ["restore evidence must be a list or contain drills"])
    passed = 0
    failures: list[str] = []
    for drill in drills:
        if not isinstance(drill, dict):
            failures.append("restore drill entry must be a mapping")
            continue
        ok = drill.get("status") == "pass"
        if gate.get("requireValidationPassed", True):
            ok = ok and drill.get("validation_passed") is True
        if ok:
            passed += 1
        else:
            failures.append(f"restore drill {drill.get('name', '<unknown>')} did not pass")
    total = len(drills)
    pass_rate = passed / total if total else 0
    if pass_rate < float(gate.get("minPassRate", 1.0)):
        failures.append(f"restore pass rate {pass_rate:.2f} below required {float(gate.get('minPassRate', 1.0)):.2f}")
    if failures:
        return fail_result("restore", rel(artifact), failures)
    return pass_result("restore", artifact, f"{passed}/{total} restore drills passed")


def check_toolchain(gate: dict[str, Any]) -> GateResult:
    artifact, early = artifact_for("toolchain", gate)
    if early:
        return early
    assert artifact is not None
    payload = load_json(artifact)
    missing_required = nested(payload, "summary", "missing_required", default=[])
    if gate.get("requireNoMissingRequired", True) and missing_required:
        return fail_result("toolchain", rel(artifact), [f"missing required tools: {missing_required}"])
    profile = payload.get("profile", "unknown")
    return pass_result("toolchain", artifact, f"toolchain profile {profile} has no missing required tools")


def check_egress(gate: dict[str, Any]) -> GateResult:
    artifact, early = artifact_for("egress", gate)
    if early:
        return early
    assert artifact is not None
    payload = load_json(artifact)
    errors = payload.get("errors", [])
    refs = payload.get("checked_references", [])
    if not isinstance(errors, list):
        return fail_result("egress", rel(artifact), ["egress errors must be a list"])
    if not isinstance(refs, list):
        return fail_result("egress", rel(artifact), ["egress checked_references must be a list"])
    failures: list[str] = []
    if len(errors) > int(gate.get("maxErrors", 0)):
        failures.append(f"egress governance has {len(errors)} errors")
    if len(refs) < int(gate.get("minReferences", 0)):
        failures.append(f"egress references {len(refs)} below minReferences {int(gate.get('minReferences', 0))}")
    if failures:
        return fail_result("egress", rel(artifact), failures)
    return pass_result("egress", artifact, f"{len(refs)} external egress references checked, {len(errors)} errors")


def check_retention(gate: dict[str, Any]) -> GateResult:
    artifact, early = artifact_for("retention", gate)
    if early:
        return early
    assert artifact is not None
    payload = load_json(artifact)
    errors = payload.get("errors", [])
    classes = payload.get("classes_checked", [])
    if not isinstance(errors, list):
        return fail_result("retention", rel(artifact), ["retention errors must be a list"])
    if not isinstance(classes, list):
        return fail_result("retention", rel(artifact), ["retention classes_checked must be a list"])
    failures: list[str] = []
    if len(errors) > int(gate.get("maxErrors", 0)):
        failures.append(f"retention has {len(errors)} errors")
    if len(classes) < int(gate.get("minClasses", 1)):
        failures.append(f"retention classes {len(classes)} below minClasses {int(gate.get('minClasses', 1))}")
    if failures:
        return fail_result("retention", rel(artifact), failures)
    return pass_result("retention", artifact, f"{len(classes)} retention classes checked, {len(errors)} errors")


def check_slo(gate: dict[str, Any]) -> GateResult:
    artifact, early = artifact_for("slo", gate)
    if early:
        return early
    assert artifact is not None
    payload = load_json(artifact)
    errors = payload.get("errors", [])
    objectives = payload.get("objectives", [])
    if not isinstance(errors, list):
        return fail_result("slo", rel(artifact), ["SLO errors must be a list"])
    if not isinstance(objectives, list):
        return fail_result("slo", rel(artifact), ["SLO objectives must be a list"])
    failed = int(nested(payload, "summary", "failed", default=0))
    failures: list[str] = []
    if len(errors) > int(gate.get("maxErrors", 0)):
        failures.append(f"SLO report has {len(errors)} config errors")
    if failed > 0:
        failures.append(f"SLO report has {failed} failed objectives")
    if len(objectives) < int(gate.get("minObjectives", 1)):
        failures.append(f"SLO objectives {len(objectives)} below minObjectives {int(gate.get('minObjectives', 1))}")
    if failures:
        return fail_result("slo", rel(artifact), failures)
    passed = int(nested(payload, "summary", "passed", default=0))
    return pass_result("slo", artifact, f"{passed}/{len(objectives)} SLO objectives passed, {len(errors)} config errors")


def check_quota(gate: dict[str, Any]) -> GateResult:
    artifact, early = artifact_for("quota", gate)
    if early:
        return early
    assert artifact is not None
    payload = load_json(artifact)
    errors = payload.get("errors", [])
    plans = payload.get("plans_checked", [])
    labels = payload.get("chargeback_labels", [])
    if not isinstance(errors, list):
        return fail_result("quota", rel(artifact), ["quota errors must be a list"])
    if not isinstance(plans, list):
        return fail_result("quota", rel(artifact), ["quota plans_checked must be a list"])
    if not isinstance(labels, list):
        return fail_result("quota", rel(artifact), ["quota chargeback_labels must be a list"])
    failures: list[str] = []
    if len(errors) > int(gate.get("maxErrors", 0)):
        failures.append(f"quota report has {len(errors)} errors")
    if len(plans) < int(gate.get("minPlans", 1)):
        failures.append(f"quota plans {len(plans)} below minPlans {int(gate.get('minPlans', 1))}")
    if len(labels) < int(gate.get("minLabels", 1)):
        failures.append(f"quota labels {len(labels)} below minLabels {int(gate.get('minLabels', 1))}")
    if failures:
        return fail_result("quota", rel(artifact), failures)
    return pass_result("quota", artifact, f"{len(plans)} quota plans and {len(labels)} chargeback labels checked")


def check_model_provenance(gate: dict[str, Any]) -> GateResult:
    artifact, early = artifact_for("modelProvenance", gate)
    if early:
        return early
    assert artifact is not None
    payload = load_json(artifact)
    errors = payload.get("errors", [])
    artifacts = payload.get("artifacts_checked", [])
    approved = payload.get("approved_models_checked", [])
    if not isinstance(errors, list):
        return fail_result("modelProvenance", rel(artifact), ["model provenance errors must be a list"])
    if not isinstance(artifacts, list):
        return fail_result("modelProvenance", rel(artifact), ["model provenance artifacts_checked must be a list"])
    if not isinstance(approved, list):
        return fail_result("modelProvenance", rel(artifact), ["model provenance approved_models_checked must be a list"])
    failures: list[str] = []
    if len(errors) > int(gate.get("maxErrors", 0)):
        failures.append(f"model provenance has {len(errors)} errors")
    if len(artifacts) < int(gate.get("minArtifacts", 1)):
        failures.append(f"model provenance artifacts {len(artifacts)} below minArtifacts {int(gate.get('minArtifacts', 1))}")
    missing = sorted(set(approved) - set(artifacts))
    if missing:
        failures.append(f"approved models missing provenance: {missing}")
    if failures:
        return fail_result("modelProvenance", rel(artifact), failures)
    return pass_result("modelProvenance", artifact, f"{len(artifacts)} model provenance artifacts checked")


def load_supply_chain_validator() -> Any:
    spec = importlib.util.spec_from_file_location("supply_chain_evidence", SUPPLY_CHAIN_VALIDATOR)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {rel(SUPPLY_CHAIN_VALIDATOR)}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_supply_chain(gate: dict[str, Any]) -> GateResult:
    artifact, early = artifact_for("supplyChain", gate)
    if early:
        return early
    assert artifact is not None
    validator = load_supply_chain_validator()
    failures = validator.validate_summary(artifact, strict_current=False)
    if failures:
        return fail_result("supplyChain", rel(artifact), failures)
    payload = load_json(artifact)
    images = payload.get("images", [])
    image_count = len(images) if isinstance(images, list) else 0
    return pass_result("supplyChain", artifact, f"{image_count} images have validated SBOM, SARIF, and checksum evidence")


def check_evidence_pack(gate: dict[str, Any]) -> GateResult:
    artifact, early = artifact_for("evidencePack", gate)
    if early:
        return early
    assert artifact is not None
    payload = load_json(artifact)
    failed = int(nested(payload, "summary", "failed", default=0))
    max_failed = int(gate.get("maxFailedControls", 0))
    if failed > max_failed:
        return fail_result("evidencePack", rel(artifact), [f"evidence pack has {failed} failed controls"])
    passed = int(nested(payload, "summary", "passed", default=0))
    return pass_result("evidencePack", artifact, f"evidence pack has {passed} passed and {failed} failed controls")


CHECKS = {
    "eval": check_eval,
    "load": check_load,
    "restore": check_restore,
    "toolchain": check_toolchain,
    "egress": check_egress,
    "retention": check_retention,
    "slo": check_slo,
    "quota": check_quota,
    "modelProvenance": check_model_provenance,
    "supplyChain": check_supply_chain,
    "evidencePack": check_evidence_pack,
}


def run_gates(
    config: dict[str, Any],
    *,
    require_current_evidence: bool = False,
    max_evidence_age_hours: float | None = None,
) -> list[GateResult]:
    gates = nested(config, "spec", "gates", default={})
    results: list[GateResult] = []
    for name in CHECKS:
        if name not in gates:
            continue
        gate = dict(gates[name])
        gate["_require_current_evidence"] = require_current_evidence
        gate["_max_evidence_age_hours"] = max_evidence_age_hours
        results.append(CHECKS[name](gate))
    return results


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def write_markdown(
    path: Path,
    generated_at: str,
    config_path: Path,
    results: list[GateResult],
    *,
    require_current_evidence: bool,
    max_evidence_age_hours: float | None,
) -> None:
    passed = sum(1 for result in results if result.status == "pass")
    failed = sum(1 for result in results if result.status == "fail")
    lines = [
        "# Release Gate Report",
        "",
        f"Generated: `{generated_at}`",
        f"Config: `{rel(config_path)}`",
        f"Require current evidence: `{str(require_current_evidence).lower()}`",
        f"Max evidence age hours: `{max_evidence_age_hours if max_evidence_age_hours is not None else 'not enforced'}`",
        "",
        f"Summary: {passed} passed, {failed} failed.",
        "",
        "| Gate | Status | Summary | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    for result in results:
        lines.append(
            f"| {result.name} | {result.status} | {markdown_escape(result.summary)} | `{markdown_escape(result.evidence)}` |"
        )
    path.write_text("\n".join(lines) + "\n")


def write_json(
    path: Path,
    generated_at: str,
    config_path: Path,
    results: list[GateResult],
    *,
    require_current_evidence: bool,
    max_evidence_age_hours: float | None,
) -> None:
    payload = {
        "project": "Private AI Platform Kit",
        "generated_at": generated_at,
        "config": rel(config_path),
        "evidence_policy": {
            "require_current_evidence": require_current_evidence,
            "max_evidence_age_hours": max_evidence_age_hours,
        },
        "summary": {
            "passed": sum(1 for result in results if result.status == "pass"),
            "failed": sum(1 for result in results if result.status == "fail"),
            "skipped": sum(1 for result in results if result.status == "skip"),
        },
        "gates": [asdict(result) for result in results],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Private AI Platform Kit release gates against operational evidence.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--check", action="store_true", help="Validate and run gates without writing a report.")
    parser.add_argument("--report", action="store_true", help="Write JSON and Markdown release-gate reports.")
    parser.add_argument("--output-dir", default="results/release-gate")
    parser.add_argument(
        "--require-current-evidence",
        action="store_true",
        help="Fail when a required gate falls back to checked-in sample evidence.",
    )
    parser.add_argument(
        "--max-evidence-age-hours",
        type=float,
        default=None,
        help="Fail when selected evidence artifacts are older than this many hours.",
    )
    args = parser.parse_args()

    config_path = (ROOT / args.config).resolve() if not Path(args.config).is_absolute() else Path(args.config)
    try:
        config = load_yaml(config_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"failed to load release gate config: {exc}")
        return 1
    errors = validate_config(config)
    if errors:
        print("release gate config check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    results = run_gates(
        config,
        require_current_evidence=args.require_current_evidence,
        max_evidence_age_hours=args.max_evidence_age_hours,
    )
    failed = [result for result in results if result.status == "fail"]
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if args.report:
        output_dir = ROOT / args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        json_path = output_dir / f"release-gate-{stamp}.json"
        md_path = output_dir / f"release-gate-{stamp}.md"
        write_json(
            json_path,
            generated_at,
            config_path,
            results,
            require_current_evidence=args.require_current_evidence,
            max_evidence_age_hours=args.max_evidence_age_hours,
        )
        write_markdown(
            md_path,
            generated_at,
            config_path,
            results,
            require_current_evidence=args.require_current_evidence,
            max_evidence_age_hours=args.max_evidence_age_hours,
        )
        print(f"wrote {rel(json_path)} and {rel(md_path)}")

    if failed:
        print("release gate failed:")
        for result in failed:
            print(f"- {result.name}: {result.summary}")
        return 1
    print(f"release gate ok ({sum(1 for result in results if result.status == 'pass')} passed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
