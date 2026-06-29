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
DEFAULT_POLICY = ROOT / "platform/governance/quota-plans.yaml"


@dataclass(frozen=True)
class QuotaReport:
    generated_at: str
    policy: str
    plans_checked: list[str]
    chargeback_labels: list[str]
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


def positive_int(value: Any) -> bool:
    return isinstance(value, int) and value > 0


def check_policy_shape(policy: dict[str, Any], errors: list[str]) -> list[dict[str, Any]]:
    require(errors, policy.get("apiVersion") == "platform.ai/v1alpha1", "quota policy apiVersion must be platform.ai/v1alpha1")
    require(errors, policy.get("kind") == "QuotaPlanSet", "quota policy kind must be QuotaPlanSet")
    labels = nested(policy, "spec", "chargeback", "requiredLabels", default=[])
    required_labels = {"platform.ai/owner", "platform.ai/cost-center", "platform.ai/environment", "platform.ai/sandbox-id"}
    require(errors, isinstance(labels, list), "quota policy chargeback.requiredLabels must be a list")
    require(errors, required_labels <= set(labels or []), f"quota policy missing required chargeback labels: {sorted(required_labels - set(labels or []))}")
    plans = nested(policy, "spec", "plans", default=[])
    require(errors, isinstance(plans, list) and bool(plans), "quota policy must define spec.plans")
    if not isinstance(plans, list):
        return []
    seen: set[str] = set()
    for plan in plans:
        if not isinstance(plan, dict):
            errors.append("quota plan entries must be mappings")
            continue
        plan_id = str(plan.get("id", "<unknown>"))
        if plan_id in seen:
            errors.append(f"duplicate quota plan id {plan_id}")
        seen.add(plan_id)
        for key in ("id", "name", "environment", "namespace", "sandboxId", "owner", "costCenter", "workloadKind", "acceleratorClass"):
            require(errors, bool(plan.get(key)), f"quota plan {plan_id} must define {key}")
        require(errors, plan.get("environment") in {"local", "customer"}, f"quota plan {plan_id} environment must be local or customer")
        quota = plan.get("kubernetesQuota", {})
        require(errors, isinstance(quota, dict) and bool(quota), f"quota plan {plan_id} must define kubernetesQuota")
        for key in ("requestsCpu", "requestsMemory", "limitsCpu", "limitsMemory", "pods"):
            require(errors, bool(quota.get(key)), f"quota plan {plan_id} kubernetesQuota missing {key}")
        budget = plan.get("gatewayBudget", {})
        require(errors, isinstance(budget, dict) and bool(budget), f"quota plan {plan_id} must define gatewayBudget")
        for key in ("requestLimit", "promptCharLimit", "estimatedTokenLimit", "windowSeconds"):
            require(errors, positive_int(budget.get(key)), f"quota plan {plan_id} gatewayBudget.{key} must be a positive integer")
        source_refs = plan.get("sourceRefs", [])
        require(errors, isinstance(source_refs, list) and bool(source_refs), f"quota plan {plan_id} must define sourceRefs")
        for ref in source_refs if isinstance(source_refs, list) else []:
            require(errors, isinstance(ref, str) and bool(ref), f"quota plan {plan_id} sourceRefs must be strings")
            if isinstance(ref, str) and ref:
                require(errors, (ROOT / ref).exists(), f"quota plan {plan_id} sourceRef does not exist: {ref}")
        require(errors, bool(nested(plan, "review", "cadence")), f"quota plan {plan_id} must define review.cadence")
        require(errors, bool(nested(plan, "review", "approver")), f"quota plan {plan_id} must define review.approver")
    return [plan for plan in plans if isinstance(plan, dict)]


def check_gateway_budget_alignment(plans: list[dict[str, Any]], errors: list[str]) -> None:
    values_by_environment: dict[str, dict[str, Any]] = {}
    for environment in ("local", "customer"):
        path = ROOT / f"deploy/clusters/{environment}/values/inference-gateway.yaml"
        values_by_environment[environment] = load_yaml(path)
    for plan in plans:
        plan_id = str(plan.get("id", "<unknown>"))
        environment = str(plan.get("environment"))
        gateway = values_by_environment.get(environment, {})
        configured = gateway.get("budget", {})
        budget = plan.get("gatewayBudget", {})
        require(errors, configured.get("enabled") is True, f"{environment} gateway budget must be enabled for quota plan {plan_id}")
        require(errors, configured.get("backend") == "redis", f"{environment} gateway budget backend must be redis for quota plan {plan_id}")
        for plan_key, gateway_key in (
            ("requestLimit", "requestLimit"),
            ("promptCharLimit", "promptCharLimit"),
            ("estimatedTokenLimit", "estimatedTokenLimit"),
            ("windowSeconds", "windowSeconds"),
        ):
            if positive_int(budget.get(plan_key)) and positive_int(configured.get(gateway_key)):
                require(
                    errors,
                    int(budget[plan_key]) <= int(configured[gateway_key]),
                    f"quota plan {plan_id} {plan_key} exceeds {environment} gateway budget.{gateway_key}",
                )


def check_tenant_onboarding_alignment(plans: list[dict[str, Any]], errors: list[str]) -> None:
    plan = next((item for item in plans if item.get("id") == "coding-agents-lab"), None)
    require(errors, plan is not None, "quota policy must include coding-agents-lab plan")
    if plan is None:
        return
    onboarding_path = ROOT / "tenants/onboarding/coding-agents.yaml"
    onboarding = load_yaml(onboarding_path)
    tenant = nested(onboarding, "spec", "tenant", default={})
    require(errors, plan.get("namespace") == tenant.get("namespace"), "coding-agents quota plan namespace must match tenant onboarding namespace")
    require(errors, plan.get("sandboxId") == tenant.get("id"), "coding-agents quota plan sandboxId must match tenant onboarding tenant.id")
    require(errors, plan.get("owner") == tenant.get("owner"), "coding-agents quota plan owner must match tenant onboarding owner")
    require(errors, plan.get("costCenter") == tenant.get("costCenter"), "coding-agents quota plan costCenter must match tenant onboarding costCenter")
    require(errors, plan.get("environment") == tenant.get("environment"), "coding-agents quota plan environment must match tenant onboarding environment")

    plan_quota = plan.get("kubernetesQuota", {})
    onboarding_quota = nested(onboarding, "spec", "quotas", default={})
    quota_keys = {
        "requestsCpu": "requestsCpu",
        "requestsMemory": "requestsMemory",
        "limitsCpu": "limitsCpu",
        "limitsMemory": "limitsMemory",
        "pods": "pods",
        "persistentVolumeClaims": "persistentVolumeClaims",
        "secrets": "secrets",
        "configMaps": "configMaps",
    }
    for plan_key, onboarding_key in quota_keys.items():
        require(
            errors,
            str(plan_quota.get(plan_key)) == str(onboarding_quota.get(onboarding_key)),
            f"coding-agents quota plan {plan_key} must match tenant onboarding quotas.{onboarding_key}",
        )
    require(
        errors,
        str(nested(plan, "workspace", "pvcSize")) == str(nested(onboarding, "spec", "agentWorkspace", "pvcSize")),
        "coding-agents quota plan workspace.pvcSize must match tenant onboarding agentWorkspace.pvcSize",
    )


def check_chargeback_label_coverage(labels: list[str], errors: list[str]) -> None:
    files = [
        ROOT / "deploy/policies/kyverno/policies.yaml",
        ROOT / "tenants/examples/team-a-lab.yaml",
        ROOT / "runbooks/budget-controls.md",
    ]
    for label in labels:
        for path in files:
            if path.exists():
                require(errors, label in path.read_text(), f"{path.relative_to(ROOT)} must reference chargeback label {label}")


def run_check(policy_path: Path) -> QuotaReport:
    errors: list[str] = []
    policy = load_yaml(policy_path)
    plans = check_policy_shape(policy, errors)
    labels = nested(policy, "spec", "chargeback", "requiredLabels", default=[])
    labels = labels if isinstance(labels, list) else []
    if plans:
        check_gateway_budget_alignment(plans, errors)
        check_tenant_onboarding_alignment(plans, errors)
    if labels:
        check_chargeback_label_coverage(labels, errors)
    return QuotaReport(
        generated_at=datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        policy=rel(policy_path),
        plans_checked=[str(plan.get("id")) for plan in plans if plan.get("id")],
        chargeback_labels=[str(label) for label in labels],
        errors=errors,
    )


def write_json(path: Path, report: QuotaReport) -> None:
    path.write_text(json.dumps(asdict(report), indent=2, sort_keys=True) + "\n")


def write_markdown(path: Path, report: QuotaReport) -> None:
    lines = [
        "# Quota And Chargeback Report",
        "",
        f"Generated: `{report.generated_at}`",
        f"Policy: `{report.policy}`",
        "",
        f"Summary: {len(report.plans_checked)} plans checked, {len(report.chargeback_labels)} labels checked, {len(report.errors)} errors.",
        "",
        "| Plan | Status |",
        "| --- | --- |",
    ]
    for plan in report.plans_checked:
        lines.append(f"| {plan} | {'fail' if report.errors else 'pass'} |")
    if report.errors:
        lines.extend(["", "## Errors", ""])
        for error in report.errors:
            lines.append(f"- {error}")
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate quota, budget, and chargeback governance for Private AI Platform Kit.")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--output-dir", default=".out/results/quota")
    args = parser.parse_args()

    policy_path = Path(args.policy)
    if not policy_path.is_absolute():
        policy_path = ROOT / policy_path
    report = run_check(policy_path)

    if args.report:
        output_dir = ROOT / args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        json_path = output_dir / f"quota-{stamp}.json"
        md_path = output_dir / f"quota-{stamp}.md"
        write_json(json_path, report)
        write_markdown(md_path, report)
        print(f"wrote {rel(json_path)} and {rel(md_path)}")

    if report.errors:
        print("quota check failed:")
        for error in report.errors:
            print(f"- {error}")
        return 1
    print(f"quota OK: {len(report.plans_checked)} plan(s), {len(report.chargeback_labels)} chargeback label(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
