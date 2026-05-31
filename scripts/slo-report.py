#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "slo/objectives.yaml"


@dataclass(frozen=True)
class ObjectiveResult:
    id: str
    service: str
    indicator: str
    status: str
    summary: str
    evidence: str
    failures: list[str]


@dataclass(frozen=True)
class SloReport:
    generated_at: str
    config: str
    profile: str
    errors: list[str]
    objectives: list[ObjectiveResult]


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


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


def latest_artifact(patterns: list[str]) -> Path | None:
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(path for path in ROOT.glob(pattern) if path.is_file())
    if not candidates:
        return None
    non_sample = [path for path in candidates if not path.name.startswith("sample-")]
    return max(non_sample or candidates, key=lambda path: path.stat().st_mtime)


def validate_alert_refs(config: dict[str, Any], errors: list[str]) -> None:
    alert_path = ROOT / "observability/alerts/ai-platform-alerts.yaml"
    if not alert_path.exists():
        errors.append("observability alert rules must exist")
        return
    docs = [doc for doc in yaml.safe_load_all(alert_path.read_text()) if isinstance(doc, dict)]
    alert_names: set[str] = set()
    for doc in docs:
        for group in nested(doc, "spec", "groups", default=[]):
            if not isinstance(group, dict):
                continue
            for rule in group.get("rules", []):
                if isinstance(rule, dict) and rule.get("alert"):
                    alert_names.add(rule["alert"])
    for objective in nested(config, "spec", "objectives", default=[]):
        if not isinstance(objective, dict):
            continue
        for alert in objective.get("alertRefs", []):
            if alert not in alert_names:
                errors.append(f"objective {objective.get('id', '<unknown>')} references missing alert {alert}")


def validate_config(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if config.get("apiVersion") != "platform.ai/v1alpha1":
        errors.append("SLO config apiVersion must be platform.ai/v1alpha1")
    if config.get("kind") != "SLOSet":
        errors.append("SLO config kind must be SLOSet")
    objectives = nested(config, "spec", "objectives", default=[])
    if not isinstance(objectives, list) or len(objectives) < 5:
        errors.append("SLO config must define at least five objectives")
        return errors
    seen: set[str] = set()
    allowed_indicators = {
        "load-error-rate",
        "load-latency",
        "eval-pass-rate",
        "restore-pass-rate",
        "evidence-controls",
    }
    for objective in objectives:
        if not isinstance(objective, dict):
            errors.append("each SLO objective must be a mapping")
            continue
        objective_id = objective.get("id", "<unknown>")
        if objective_id in seen:
            errors.append(f"duplicate SLO objective id {objective_id}")
        seen.add(objective_id)
        if not isinstance(objective.get("id"), str) or not objective.get("id"):
            errors.append("SLO objective id must be set")
        if not isinstance(objective.get("service"), str) or not objective.get("service"):
            errors.append(f"objective {objective_id} service must be set")
        if objective.get("indicator") not in allowed_indicators:
            errors.append(f"objective {objective_id} indicator must be one of {sorted(allowed_indicators)}")
        target = objective.get("target")
        if not isinstance(target, dict) or not target:
            errors.append(f"objective {objective_id} target must be a non-empty mapping")
        evidence = objective.get("evidence")
        if not isinstance(evidence, list) or not all(isinstance(item, str) and item for item in evidence):
            errors.append(f"objective {objective_id} evidence must be a non-empty string list")
        alert_refs = objective.get("alertRefs", [])
        if not isinstance(alert_refs, list) or not all(isinstance(item, str) for item in alert_refs):
            errors.append(f"objective {objective_id} alertRefs must be a string list")
    validate_alert_refs(config, errors)
    return errors


def metric(payload: dict[str, Any], name: str, key: str, default: float = 0.0) -> float:
    value = nested(payload, "metrics", name, key, default=default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def fail_result(objective: dict[str, Any], evidence: str, failures: list[str]) -> ObjectiveResult:
    return ObjectiveResult(
        id=str(objective.get("id", "<unknown>")),
        service=str(objective.get("service", "<unknown>")),
        indicator=str(objective.get("indicator", "<unknown>")),
        status="fail",
        summary="; ".join(failures),
        evidence=evidence,
        failures=failures,
    )


def pass_result(objective: dict[str, Any], evidence: Path, summary: str) -> ObjectiveResult:
    return ObjectiveResult(
        id=str(objective.get("id")),
        service=str(objective.get("service")),
        indicator=str(objective.get("indicator")),
        status="pass",
        summary=summary,
        evidence=rel(evidence),
        failures=[],
    )


def artifact_for(objective: dict[str, Any]) -> tuple[Path | None, ObjectiveResult | None]:
    artifact = latest_artifact(list(objective.get("evidence", [])))
    if artifact is None:
        return None, fail_result(objective, "", ["missing required SLO evidence artifact"])
    return artifact, None


def check_load_error_rate(objective: dict[str, Any], artifact: Path) -> ObjectiveResult:
    payload = load_json(artifact)
    target = objective.get("target", {})
    requests = metric(payload, "http_reqs", "count")
    error_rate = metric(payload, "http_req_failed", "rate")
    failures: list[str] = []
    if requests < float(target.get("minRequests", 1)):
        failures.append(f"requests {requests:g} below minRequests {float(target.get('minRequests', 1)):g}")
    if error_rate > float(target.get("maxErrorRate", 0.05)):
        failures.append(f"error rate {error_rate:.4f} exceeds {float(target.get('maxErrorRate', 0.05)):.4f}")
    if failures:
        return fail_result(objective, rel(artifact), failures)
    return pass_result(objective, artifact, f"{requests:g} requests, error rate {error_rate:.4f}")


def check_load_latency(objective: dict[str, Any], artifact: Path) -> ObjectiveResult:
    payload = load_json(artifact)
    target = objective.get("target", {})
    requests = metric(payload, "http_reqs", "count")
    p95 = metric(payload, "http_req_duration", "p(95)")
    p99 = metric(payload, "http_req_duration", "p(99)")
    failures: list[str] = []
    if requests < float(target.get("minRequests", 1)):
        failures.append(f"requests {requests:g} below minRequests {float(target.get('minRequests', 1)):g}")
    if p95 > float(target.get("maxP95LatencyMs", 10000)):
        failures.append(f"p95 {p95:.2f}ms exceeds {float(target.get('maxP95LatencyMs', 10000)):.2f}ms")
    if p99 > float(target.get("maxP99LatencyMs", 15000)):
        failures.append(f"p99 {p99:.2f}ms exceeds {float(target.get('maxP99LatencyMs', 15000)):.2f}ms")
    if failures:
        return fail_result(objective, rel(artifact), failures)
    return pass_result(objective, artifact, f"p95 {p95:.2f}ms, p99 {p99:.2f}ms")


def check_eval_pass_rate(objective: dict[str, Any], artifact: Path) -> ObjectiveResult:
    payload = load_json(artifact)
    target = objective.get("target", {})
    results = payload.get("results", [])
    if not isinstance(results, list):
        return fail_result(objective, rel(artifact), ["eval evidence results must be a list"])
    passed = sum(1 for item in results if isinstance(item, dict) and item.get("passed") is True)
    total = len(results)
    pass_rate = passed / total if total else 0.0
    max_latency = float(target.get("maxCaseLatencyMs", 30000))
    failures: list[str] = []
    if total < int(target.get("minCases", 1)):
        failures.append(f"eval cases {total} below minCases {int(target.get('minCases', 1))}")
    if pass_rate < float(target.get("minPassRate", 1.0)):
        failures.append(f"pass rate {pass_rate:.2f} below {float(target.get('minPassRate', 1.0)):.2f}")
    for item in results:
        if isinstance(item, dict) and float(item.get("latency_ms", 0)) > max_latency:
            failures.append(f"eval case {item.get('case_id')} latency exceeded {max_latency}ms")
    if failures:
        return fail_result(objective, rel(artifact), failures)
    return pass_result(objective, artifact, f"{passed}/{total} eval cases passed")


def check_restore_pass_rate(objective: dict[str, Any], artifact: Path) -> ObjectiveResult:
    payload = load_json(artifact)
    drills = payload if isinstance(payload, list) else payload.get("drills", [])
    if not isinstance(drills, list):
        return fail_result(objective, rel(artifact), ["restore evidence must be a list or contain drills"])
    target = objective.get("target", {})
    passed = 0
    failures: list[str] = []
    for drill in drills:
        if not isinstance(drill, dict):
            failures.append("restore drill entry must be a mapping")
            continue
        ok = drill.get("status") == "pass"
        if target.get("requireValidationPassed", True):
            ok = ok and drill.get("validation_passed") is True
        if ok:
            passed += 1
        else:
            failures.append(f"restore drill {drill.get('name', '<unknown>')} did not pass")
    total = len(drills)
    pass_rate = passed / total if total else 0.0
    if pass_rate < float(target.get("minPassRate", 1.0)):
        failures.append(f"restore pass rate {pass_rate:.2f} below {float(target.get('minPassRate', 1.0)):.2f}")
    if failures:
        return fail_result(objective, rel(artifact), failures)
    return pass_result(objective, artifact, f"{passed}/{total} restore drills passed")


def check_evidence_controls(objective: dict[str, Any], artifact: Path) -> ObjectiveResult:
    payload = load_json(artifact)
    target = objective.get("target", {})
    failed = int(nested(payload, "summary", "failed", default=0))
    failures: list[str] = []
    max_failed = int(target.get("maxFailedControls", 0))
    if failed > max_failed:
        failures.append(f"evidence pack has {failed} failed controls")
    controls = payload.get("controls", [])
    control_names = {item.get("area") for item in controls if isinstance(item, dict)}
    required_controls = target.get("requiredControls", [])
    if control_names:
        missing = sorted(set(required_controls) - control_names)
        if missing:
            failures.append(f"evidence pack missing required controls: {missing}")
    if failures:
        return fail_result(objective, rel(artifact), failures)
    passed = int(nested(payload, "summary", "passed", default=0))
    return pass_result(objective, artifact, f"evidence pack has {passed} passed and {failed} failed controls")


CHECKS = {
    "load-error-rate": check_load_error_rate,
    "load-latency": check_load_latency,
    "eval-pass-rate": check_eval_pass_rate,
    "restore-pass-rate": check_restore_pass_rate,
    "evidence-controls": check_evidence_controls,
}


def run_objectives(config: dict[str, Any]) -> list[ObjectiveResult]:
    results: list[ObjectiveResult] = []
    for objective in nested(config, "spec", "objectives", default=[]):
        if not isinstance(objective, dict):
            continue
        artifact, early = artifact_for(objective)
        if early is not None:
            results.append(early)
            continue
        assert artifact is not None
        check = CHECKS.get(str(objective.get("indicator")))
        if check is None:
            results.append(fail_result(objective, rel(artifact), ["unsupported SLO indicator"]))
            continue
        results.append(check(objective, artifact))
    return results


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def write_markdown(path: Path, report: SloReport) -> None:
    passed = sum(1 for item in report.objectives if item.status == "pass")
    failed = sum(1 for item in report.objectives if item.status == "fail")
    lines = [
        "# SLO And Error Budget Report",
        "",
        f"Generated: `{report.generated_at}`",
        f"Config: `{report.config}`",
        f"Profile: `{report.profile}`",
        "",
        f"Summary: {passed} passed, {failed} failed, {len(report.errors)} config errors.",
        "",
        "| Objective | Service | Status | Summary | Evidence |",
        "| --- | --- | --- | --- | --- |",
    ]
    for result in report.objectives:
        lines.append(
            f"| {markdown_escape(result.id)} | {markdown_escape(result.service)} | {result.status} | {markdown_escape(result.summary)} | `{markdown_escape(result.evidence)}` |"
        )
    if report.errors:
        lines.extend(["", "## Config Errors", ""])
        for error in report.errors:
            lines.append(f"- {error}")
    path.write_text("\n".join(lines) + "\n")


def write_json(path: Path, report: SloReport) -> None:
    passed = sum(1 for item in report.objectives if item.status == "pass")
    failed = sum(1 for item in report.objectives if item.status == "fail")
    payload = {
        "project": "AI Platform Ops Lab",
        "generated_at": report.generated_at,
        "config": report.config,
        "profile": report.profile,
        "summary": {
            "passed": passed,
            "failed": failed,
            "config_errors": len(report.errors),
        },
        "errors": report.errors,
        "objectives": [asdict(item) for item in report.objectives],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def build_report(config_path: Path) -> SloReport:
    config = load_yaml(config_path)
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    errors = validate_config(config)
    objectives = [] if errors else run_objectives(config)
    return SloReport(
        generated_at=generated_at,
        config=rel(config_path),
        profile=str(nested(config, "spec", "profile", default="unknown")),
        errors=errors,
        objectives=objectives,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Check AI Platform Ops Lab SLO objectives against current evidence.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--output-dir", default="results/slo")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    report = build_report(config_path)

    if args.report:
        output_dir = ROOT / args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        json_path = output_dir / f"slo-{stamp}.json"
        md_path = output_dir / f"slo-{stamp}.md"
        write_json(json_path, report)
        write_markdown(md_path, report)
        print(f"wrote {rel(json_path)} and {rel(md_path)}")

    failed = [item for item in report.objectives if item.status == "fail"]
    if report.errors or failed:
        print("SLO check failed:")
        for error in report.errors:
            print(f"- {error}")
        for item in failed:
            print(f"- {item.id}: {item.summary}")
        return 1

    print(f"SLO OK: {len(report.objectives)} objective(s) checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
