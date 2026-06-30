#!/usr/bin/env python3
"""Generate a tenant deprovisioning (offboarding) plan from a TenantOnboarding spec.

Onboarding created namespaces, quotas, network policies, workspaces, budgets, and RAG
sources for a tenant; there was no counterpart to tear them down, so offboarded tenants
left orphaned namespaces, budget counters, and vectors. This emits an ordered, auditable
teardown plan (the operator runs the commands, matching the kit's manifests-and-runbooks
boundary) and never deletes audit/evidence, which is retained per the retention policy.

Usage:
  scripts/tenant-offboard.py --spec tenants/onboarding/<tenant>.yaml [--check] [--format text|json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

DEFAULT_BUDGET_KEY_PREFIX = "private-ai-platform-kit:sandbox-budget"


def load_spec(path: Path) -> dict[str, Any]:
    """Load and minimally validate a TenantOnboarding spec for offboarding."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    if data.get("apiVersion") != "platform.ai/v1alpha1":
        raise ValueError("apiVersion must be platform.ai/v1alpha1")
    if data.get("kind") != "TenantOnboarding":
        raise ValueError("kind must be TenantOnboarding")
    spec = data.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("spec must be a mapping")
    tenant = spec.get("tenant")
    if not isinstance(tenant, dict):
        raise ValueError("spec.tenant must be a mapping")
    for field in ("id", "namespace"):
        if not tenant.get(field):
            raise ValueError(f"spec.tenant.{field} is required for offboarding")
    return data


def build_plan(spec: dict[str, Any], budget_key_prefix: str) -> dict[str, Any]:
    """Build an ordered deprovisioning plan from the onboarding spec."""
    body = spec["spec"]
    tenant = body["tenant"]
    namespace = str(tenant["namespace"])
    sandbox_id = str(tenant["id"])
    rag_sources = [str(item) for item in body.get("ragSources", []) if str(item)]

    steps: list[dict[str, str]] = [
        {
            "order": "1",
            "action": "revoke-access",
            "detail": "Remove the tenant's gateway API key digest from auth.apiKeyHashes "
            "(and any sandbox policy) and sync, so no new requests are admitted.",
        },
        {
            "order": "2",
            "action": "purge-sandbox-budget",
            "detail": f"redis-cli -u $SANDBOX_BUDGET_REDIS_URL DEL "
            f"{budget_key_prefix}:{sandbox_id} {budget_key_prefix}:ratelimit:{sandbox_id}",
        },
    ]
    for index, source_id in enumerate(rag_sources, start=3):
        steps.append(
            {
                "order": str(index),
                "action": "purge-rag-vectors",
                "detail": f"python scripts/rag-ingest.py --delete --source-id {source_id} "
                f"--qdrant-url $QDRANT_URL --collection $QDRANT_COLLECTION",
            }
        )
    next_order = 3 + len(rag_sources)
    steps.append(
        {
            "order": str(next_order),
            "action": "delete-namespace",
            "detail": f"kubectl delete namespace {namespace}  "
            "# removes workspaces, PVCs, quotas, limit ranges, and network policies",
        }
    )
    steps.append(
        {
            "order": str(next_order + 1),
            "action": "retain-evidence",
            "detail": "Do NOT delete audit logs or evidence; retain them per "
            "platform/governance/data-retention.yaml and record the offboarding date.",
        }
    )
    return {
        "tenant": {"id": sandbox_id, "namespace": namespace},
        "ragSources": rag_sources,
        "steps": steps,
    }


def render_text(plan: dict[str, Any]) -> str:
    lines = [
        f"Tenant offboarding plan for '{plan['tenant']['id']}' (namespace {plan['tenant']['namespace']}):",
        "",
    ]
    for step in plan["steps"]:
        lines.append(f"  {step['order']}. [{step['action']}] {step['detail']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a tenant deprovisioning plan.")
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--check", action="store_true", help="Validate the spec only.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--budget-key-prefix", default=DEFAULT_BUDGET_KEY_PREFIX)
    args = parser.parse_args()

    spec = load_spec(args.spec)
    if args.check:
        print(f"tenant offboarding spec OK: {args.spec} ({spec['spec']['tenant']['id']})")
        return 0

    plan = build_plan(spec, args.budget_key_prefix)
    if args.format == "json":
        print(json.dumps(plan, indent=2, sort_keys=True))
    else:
        print(render_text(plan), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
