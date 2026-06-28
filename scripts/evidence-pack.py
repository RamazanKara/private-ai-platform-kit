#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Control:
    area: str
    status: str
    summary: str
    evidence: list[str]
    customer_action: str


@dataclass(frozen=True)
class Artifact:
    name: str
    path: str
    kind: str
    source: str


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def read_text(path: str) -> str:
    target = ROOT / path
    return target.read_text() if target.exists() else ""


def load_yaml(path: str) -> Any:
    target = ROOT / path
    if not target.exists():
        return {}
    return yaml.safe_load(target.read_text()) or {}


def nested(mapping: Any, *keys: str, default: Any = None) -> Any:
    current = mapping
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return current if current is not None else default


def exists(*paths: str) -> bool:
    return all((ROOT / path).exists() for path in paths)


def executable(path: str) -> bool:
    target = ROOT / path
    return target.exists() and target.stat().st_mode & 0o111 != 0


def latest_artifact(name: str, patterns: list[str], kind: str) -> Artifact | None:
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(path for path in ROOT.glob(pattern) if path.is_file())
    if not candidates:
        return None
    non_sample = [path for path in candidates if not path.name.startswith("sample-")]
    selected = max(non_sample or candidates, key=lambda path: path.stat().st_mtime)
    source = "latest-run" if not selected.name.startswith("sample-") else "sample"
    return Artifact(name=name, path=rel(selected), kind=kind, source=source)


def collect_artifacts() -> list[Artifact]:
    artifacts = [
        Artifact("Project README", "README.md", "markdown", "tracked"),
        Artifact("Production readiness matrix", "docs/production-readiness.md", "markdown", "tracked"),
        Artifact("API access runbook", "runbooks/api-access.md", "markdown", "tracked"),
        Artifact("Gateway OpenAPI contract", "api-contracts/inference-gateway.openapi.json", "json", "tracked"),
        Artifact("RAG OpenAPI contract", "api-contracts/rag-service.openapi.json", "json", "tracked"),
        Artifact("Gateway configuration contract", "config-contracts/inference-gateway.config.json", "json", "tracked"),
        Artifact("RAG configuration contract", "config-contracts/rag-service.config.json", "json", "tracked"),
        Artifact("Agent workspaces runbook", "runbooks/agent-workspaces.md", "markdown", "tracked"),
        Artifact("Tenant onboarding spec", "tenants/onboarding/coding-agents.yaml", "yaml", "tracked"),
        Artifact("Regulated offline tenant onboarding spec", "tenants/onboarding/regulated-offline-coding-agents.yaml", "yaml", "tracked"),
        Artifact("Qdrant vector-store chart", "charts/qdrant-vector-store/", "helm-chart", "tracked"),
        Artifact("Vector RAG runbook", "runbooks/vector-rag.md", "markdown", "tracked"),
        Artifact("Gateway guardrails runbook", "runbooks/guardrails.md", "markdown", "tracked"),
        Artifact("Model governance runbook", "runbooks/model-governance.md", "markdown", "tracked"),
        Artifact("Model provenance policy", "governance/model-provenance.yaml", "yaml", "tracked"),
        Artifact("Model provenance runbook", "runbooks/model-provenance.md", "markdown", "tracked"),
        Artifact("Validation toolchain manifest", "tools/validation-toolchain.yaml", "yaml", "tracked"),
        Artifact("Validation toolchain installer", "scripts/install-validation-tools.sh", "shell", "tracked"),
        Artifact("Validation toolchain runbook", "runbooks/validation-toolchain.md", "markdown", "tracked"),
        Artifact("Release gate definition", "slo/release-gates.yaml", "yaml", "tracked"),
        Artifact("Release gates runbook", "runbooks/release-gates.md", "markdown", "tracked"),
        Artifact("SLO objective definition", "slo/objectives.yaml", "yaml", "tracked"),
        Artifact("SLO and error budget runbook", "runbooks/slo-error-budget.md", "markdown", "tracked"),
        Artifact("Quota plan policy", "governance/quota-plans.yaml", "yaml", "tracked"),
        Artifact("Quota and chargeback runbook", "runbooks/quota-chargeback.md", "markdown", "tracked"),
        Artifact("Egress catalog", "network/egress-catalog.yaml", "yaml", "tracked"),
        Artifact("Egress governance runbook", "runbooks/egress-governance.md", "markdown", "tracked"),
        Artifact("Data retention policy", "governance/data-retention.yaml", "yaml", "tracked"),
        Artifact("Data retention runbook", "runbooks/data-retention.md", "markdown", "tracked"),
        Artifact("Chaos drill catalog", "chaos/drills/", "yaml", "tracked"),
        Artifact("Restore drill runbook", "runbooks/restore-drill.md", "markdown", "tracked"),
    ]
    for artifact in [
        latest_artifact("Evaluation summary", ["results/evals/*.md"], "markdown"),
        latest_artifact("Model governance summary", ["results/model-catalog/*.md"], "markdown"),
        latest_artifact("Model provenance summary", ["results/model-provenance/*.md"], "markdown"),
        latest_artifact("Validation toolchain report", ["results/toolchain/*.md"], "markdown"),
        latest_artifact("SLO and error budget report", ["results/slo/*.md"], "markdown"),
        latest_artifact("Quota and chargeback report", ["results/quota/*.md"], "markdown"),
        latest_artifact("Egress governance report", ["results/egress-governance/*.md"], "markdown"),
        latest_artifact("Data retention report", ["results/retention/*.md"], "markdown"),
        latest_artifact("Release gate report", ["results/release-gate/*.md"], "markdown"),
        latest_artifact("Supply-chain scan summary", ["results/supply-chain/*.md"], "markdown"),
        latest_artifact("Load-test summary", ["results/loadtest/*.md"], "markdown"),
        latest_artifact("Restore-drill JSON evidence", ["results/restore-drill/*run*.json", "results/restore-drill/sample-redis-run.json"], "json"),
        latest_artifact("Restore-drill compliance report", ["results/restore-drill/*compliance*.html", "results/restore-drill/sample-compliance-report.html"], "html"),
    ]:
        if artifact is not None:
            artifacts.append(artifact)
    return artifacts


def control(area: str, ok: bool, summary: str, evidence: list[str], customer_action: str) -> Control:
    return Control(area=area, status="pass" if ok else "fail", summary=summary, evidence=evidence, customer_action=customer_action)


def static_controls() -> list[Control]:
    local_gateway = load_yaml("clusters/local/values/inference-gateway.yaml")
    customer_gateway = load_yaml("clusters/customer/values/inference-gateway.yaml")
    local_rag = load_yaml("clusters/local/values/rag-service.yaml")
    customer_rag = load_yaml("clusters/customer/values/rag-service.yaml")
    local_qdrant = load_yaml("clusters/local/values/qdrant-vector-store.yaml")
    customer_qdrant = load_yaml("clusters/customer/values/qdrant-vector-store.yaml")
    vllm_amd = load_yaml("clusters/customer/values/vllm-amd.yaml")
    vllm_nvidia = load_yaml("clusters/customer/values/vllm-nvidia.yaml")
    model_catalog = load_yaml("model-catalog/models.yaml")
    model_provenance = load_yaml("governance/model-provenance.yaml")
    workflow = read_text(".github/workflows/ci.yml")
    readme = read_text("README.md")
    production_doc = read_text("docs/production-readiness.md")
    toolchain = load_yaml("tools/validation-toolchain.yaml")
    release_gates = load_yaml("slo/release-gates.yaml")
    slo_objectives = load_yaml("slo/objectives.yaml")
    quota_plans = load_yaml("governance/quota-plans.yaml")
    egress_catalog = load_yaml("network/egress-catalog.yaml")
    retention_policy = load_yaml("governance/data-retention.yaml")
    regulated_onboarding = load_yaml("tenants/onboarding/regulated-offline-coding-agents.yaml")
    chaos_drill_names = {
        nested(item, "metadata", "name")
        for path in sorted((ROOT / "chaos/drills").glob("*.yaml"))
        for item in [load_yaml(path.relative_to(ROOT).as_posix())]
    }

    allowed = set(nested(local_gateway, "runtime", "allowedModels", default=[]))
    allowed.update(nested(customer_gateway, "runtime", "allowedModels", default=[]))
    catalog_ids = {model.get("id") for model in nested(model_catalog, "spec", "models", default=[]) if isinstance(model, dict)}

    return [
        control(
            "Local-first customer-owned Kubernetes",
            "local" in readme and "customer-owned clusters" in readme and exists("clusters/local/kind-config.yaml", "clusters/customer/README.md"),
            "The README keeps the core product local-first and portable to customer-owned clusters.",
            ["README.md", "clusters/local/kind-config.yaml", "clusters/customer/README.md"],
            "Provide the customer's ingress, storage class, secret backend, GPU nodes, and observability integrations.",
        ),
        control(
            "OpenAI-compatible gateway and API authentication",
            exists("services/inference-gateway/app/main.py", "charts/inference-gateway/templates/deployment.yaml")
            and nested(local_gateway, "auth", "enabled") is True
            and nested(customer_gateway, "auth", "enabled") is True
            and nested(customer_gateway, "auth", "existingSecret", "name"),
            "Gateway business endpoints require API keys in local and customer values.",
            ["services/inference-gateway/app/main.py", "charts/inference-gateway/", "clusters/customer/values/inference-gateway.yaml"],
            "Back customer key hashes with the customer's secret manager and rotate through External Secrets.",
        ),
        control(
            "RAG service for coding-agent grounding",
            exists("services/rag-service/app/main.py", "charts/rag-service/templates/deployment.yaml")
            and nested(local_rag, "auth", "enabled") is True
            and nested(customer_rag, "auth", "enabled") is True
            and nested(customer_rag, "autoscaling", "enabled") is True,
            "The RAG service exposes approved context and grounded messages with the same API-key pattern.",
            ["services/rag-service/app/main.py", "charts/rag-service/", "runbooks/rag-service.md"],
            "Replace or extend the bundled knowledge documents with customer-approved internal context.",
        ),
        control(
            "API contract governance",
            exists("api-contracts/inference-gateway.openapi.json", "api-contracts/rag-service.openapi.json", "scripts/api-contract.py")
            and executable("scripts/api-contract.py")
            and "api-contract:" in read_text("Makefile")
            and "createChatCompletion" in read_text("api-contracts/inference-gateway.openapi.json")
            and "queryRagContext" in read_text("api-contracts/rag-service.openapi.json")
            and "securitySchemes" in read_text("api-contracts/inference-gateway.openapi.json"),
            "Gateway and RAG OpenAPI snapshots are versioned and checked for route, schema, operation ID, and auth drift.",
            ["api-contracts/", "scripts/api-contract.py", "docs/production-readiness.md"],
            "Review contract diffs with customer integrators before changing public routes or request schemas.",
        ),
        control(
            "Configuration contract governance",
            exists("config-contracts/inference-gateway.config.json", "config-contracts/rag-service.config.json", "scripts/config-contract.py")
            and executable("scripts/config-contract.py")
            and "config-contract:" in read_text("Makefile")
            and "SANDBOX_BUDGET_REDIS_URL" in read_text("config-contracts/inference-gateway.config.json")
            and "QDRANT_VECTOR_DIMENSIONS" in read_text("config-contracts/rag-service.config.json")
            and "secretKeyRef" in read_text("charts/inference-gateway/templates/deployment.yaml")
            and "secretKeyRef" in read_text("charts/rag-service/templates/deployment.yaml"),
            "Gateway and RAG runtime configuration snapshots are versioned and checked against service settings, Helm env vars, chart defaults, and secret sourcing.",
            ["config-contracts/", "scripts/config-contract.py", "charts/inference-gateway/templates/deployment.yaml", "charts/rag-service/templates/deployment.yaml"],
            "Review configuration contract diffs before changing customer overlays, runtime endpoints, budget settings, retrieval settings, or auth secrets.",
        ),
        control(
            "Vector RAG profile",
            exists("charts/qdrant-vector-store/templates/deployment.yaml", "clusters/customer/values/qdrant-vector-store.yaml", "runbooks/vector-rag.md")
            and nested(local_rag, "retrieval", "backend") == "lexical"
            and nested(customer_rag, "retrieval", "backend") == "qdrant"
            and str(nested(customer_rag, "retrieval", "vectorStore", "url", default="")).startswith("http://qdrant-vector-store.vector.svc")
            and nested(customer_qdrant, "persistence", "enabled") is True
            and nested(local_qdrant, "persistence", "enabled") is False,
            "The local lab keeps zero-dependency lexical retrieval while customer values enable a persistent Qdrant vector-store profile.",
            ["charts/qdrant-vector-store/", "clusters/customer/values/rag-service.yaml", "clusters/customer/values/qdrant-vector-store.yaml", "runbooks/vector-rag.md"],
            "Size Qdrant storage and vector dimensions to the customer's embedding strategy before loading production knowledge.",
        ),
        control(
            "Coding-agent workspaces",
            exists("charts/agent-workspace/templates/pvc.yaml", "charts/agent-workspace/templates/rbac.yaml", "charts/agent-workspace/templates/networkpolicy.yaml")
            and executable("scripts/agent-smoke.sh"),
            "Agent workspaces include PVC-backed storage, namespace-scoped RBAC, quota, and approved egress.",
            ["charts/agent-workspace/", "clusters/customer/values/agent-workspace.yaml", "runbooks/agent-workspaces.md"],
            "Create one workspace per team, project, or trust boundary and approve any external egress explicitly.",
        ),
        control(
            "Tenant onboarding workflow",
            exists("scripts/tenant-onboard.py", "tenants/onboarding/coding-agents.yaml")
            and executable("scripts/tenant-onboard.py")
            and "TenantOnboarding" in read_text("tenants/onboarding/coding-agents.yaml"),
            "Tenant onboarding renders namespace controls and matching coding-agent workspace values from one reviewed spec.",
            ["scripts/tenant-onboard.py", "tenants/onboarding/coding-agents.yaml", "runbooks/tenant-labs.md"],
            "Review the generated namespace, quota, RBAC, workspace PVC, and egress settings before applying them to a customer cluster.",
        ),
        control(
            "Regulated offline tenant profile",
            exists("tenants/onboarding/regulated-offline-coding-agents.yaml", "scripts/tenant-onboard.py")
            and nested(regulated_onboarding, "spec", "compliance", "profile") == "regulated-offline"
            and nested(regulated_onboarding, "spec", "compliance", "externalEgressAllowed") is False
            and nested(regulated_onboarding, "spec", "network", "allowedEgressCidrs", default=[]) == []
            and nested(regulated_onboarding, "spec", "agentWorkspace", "rbac", "allowJobManagement") is False,
            "A regulated/offline onboarding profile renders coding-agent tenant controls with no external CIDR egress.",
            ["tenants/onboarding/regulated-offline-coding-agents.yaml", "scripts/tenant-onboard.py", "runbooks/tenant-labs.md"],
            "Use this profile for offline or regulated teams, then add external dependencies only through reviewed catalog-backed changes.",
        ),
        control(
            "Traceable sandbox isolation",
            exists("sandbox/base/namespace.yaml", "sandbox/base/networkpolicy.yaml", "sandbox/base/resource-controls.yaml", "sandbox/tests/trace-smoke-job.yaml")
            and executable("scripts/sandbox-smoke.sh"),
            "Sandbox namespaces carry trace labels, quota, limits, default-deny networking, and a smoke job.",
            ["sandbox/base/", "sandbox/tests/trace-smoke-job.yaml", "runbooks/traceability-sandbox.md"],
            "Preserve `X-Request-ID`, `X-Sandbox-ID`, and `traceparent` through ingress, agents, and logs.",
        ),
        control(
            "Shared sandbox budget controls",
            exists("charts/budget-redis/templates/deployment.yaml")
            and nested(local_gateway, "budget", "backend") == "redis"
            and nested(customer_gateway, "budget", "backend") == "redis"
            and nested(customer_gateway, "budget", "requestLimit", default=0) > 0,
            "Gateway replicas share Redis-compatible request, prompt-character, and estimated-token counters.",
            ["charts/budget-redis/", "clusters/customer/values/inference-gateway.yaml", "runbooks/budget-controls.md"],
            "Map budget limits to customer tenant policies and replace bundled Redis with an enterprise service if required.",
        ),
        control(
            "Model catalog and admission controls",
            exists("model-catalog/models.yaml", "model-catalog/k8s/configmap.yaml")
            and allowed <= catalog_ids
            and nested(customer_gateway, "admission", "maxPromptChars", default=0) > 0,
            "Gateway allowed models are backed by a reviewed catalog and request limits.",
            ["model-catalog/models.yaml", "clusters/customer/values/inference-gateway.yaml"],
            "Review model additions through the catalog before exposing them to tenants or coding agents.",
        ),
        control(
            "Model lifecycle governance",
            exists("scripts/model-catalog.py", "runbooks/model-governance.md", "results/model-catalog/sample-summary.md")
            and executable("scripts/model-catalog.py")
            and exists("model-catalog/promotion-requests/qwen3-local-lab-approved.yaml", "model-catalog/promotion-requests/qwen3-coder-customer-lab-approved.yaml"),
            "Approved models require promotion requests, evidence references, runtime metadata, and approved-only gateway allowlists.",
            ["scripts/model-catalog.py", "model-catalog/promotion-requests/", "runbooks/model-governance.md"],
            "Run `make model-check` before changing model status or gateway allowlists.",
        ),
        control(
            "Model provenance governance",
            exists("governance/model-provenance.yaml", "scripts/model-provenance.py", "runbooks/model-provenance.md", "results/model-provenance/sample-summary.md")
            and executable("scripts/model-provenance.py")
            and len(nested(model_provenance, "spec", "artifacts", default=[])) >= len([item for item in nested(model_catalog, "spec", "models", default=[]) if isinstance(item, dict) and item.get("status") == "approved"])
            and {"sourceUri", "immutableRef", "digest", "license", "dataClassification", "riskTier", "promotionRequest", "servingProfiles"}
            <= set(nested(model_provenance, "spec", "requiredEvidence", default=[])),
            "Approved models require source, immutable reference, digest, license, risk, data classification, promotion, serving, and evidence metadata.",
            ["governance/model-provenance.yaml", "scripts/model-provenance.py", "runbooks/model-provenance.md"],
            "Replace source-reference digests with customer model-store artifact digests before production use.",
        ),
        control(
            "Prompt secret detection",
            nested(local_gateway, "guardrails", "promptSecretDetection", "enabled") is True
            and nested(customer_gateway, "guardrails", "promptSecretDetection", "enabled") is True
            and "private_key" in nested(customer_gateway, "guardrails", "promptSecretDetection", "patterns", default=[])
            and exists("runbooks/guardrails.md"),
            "Gateway admission rejects obvious credential material before prompts reach Ollama or vLLM.",
            ["services/inference-gateway/app/settings.py", "clusters/customer/values/inference-gateway.yaml", "runbooks/guardrails.md"],
            "Keep secret detection enabled for coding-agent workspaces and tune pattern lists only after review.",
        ),
        control(
            "Validation toolchain",
            exists("tools/validation-toolchain.yaml", "scripts/toolchain-doctor.py", "scripts/install-validation-tools.sh", "runbooks/validation-toolchain.md", "results/toolchain/sample-summary.md")
            and executable("scripts/toolchain-doctor.py")
            and executable("scripts/install-validation-tools.sh")
            and {"python3", "helm", "kubeconform", "kyverno", "restore-drill", "k6", "syft", "argocd", "cosign", "trivy"}
            <= set(nested(toolchain, "spec", "profiles", "strict", "required", default=[])),
            "Validation profiles define the core, local-lab, and strict customer-handoff toolchain with a pinned installer.",
            ["tools/validation-toolchain.yaml", "scripts/toolchain-doctor.py", "scripts/install-validation-tools.sh", "runbooks/validation-toolchain.md"],
            "Run `make toolchain-install` and `make toolchain-doctor TOOLCHAIN_PROFILE=strict` before strict customer sign-off.",
        ),
        control(
            "Release gates and SLO evidence",
            exists("slo/release-gates.yaml", "scripts/release-gate.py", "runbooks/release-gates.md", "results/release-gate/sample-summary.md")
            and executable("scripts/release-gate.py")
            and "release-gate-strict" in read_text("Makefile")
            and "--require-current-evidence" in read_text("scripts/release-gate.py")
            and {"eval", "load", "restore", "toolchain", "egress", "retention", "slo", "quota", "modelProvenance", "supplyChain", "evidencePack"} <= set(nested(release_gates, "spec", "gates", default={})),
            "Customer handoff gates check eval, load, restore, strict toolchain, SLO, governance, supply-chain, evidence-pack thresholds, and strict current-evidence mode.",
            ["slo/release-gates.yaml", "scripts/release-gate.py", "runbooks/release-gates.md"],
            "Run `make release-gate-strict` before demos, releases, restore reviews, and production-readiness handoff.",
        ),
        control(
            "SLO and error budget governance",
            exists("slo/objectives.yaml", "scripts/slo-report.py", "runbooks/slo-error-budget.md", "results/slo/sample-summary.md")
            and executable("scripts/slo-report.py")
            and len(nested(slo_objectives, "spec", "objectives", default=[])) >= 5
            and "InferenceGatewayErrorBudgetFastBurn" in read_text("observability/alerts/ai-platform-alerts.yaml"),
            "SLO objectives cover inference availability, latency, eval pass rate, restore verification, and coding-agent platform readiness.",
            ["slo/objectives.yaml", "scripts/slo-report.py", "runbooks/slo-error-budget.md", "observability/alerts/ai-platform-alerts.yaml"],
            "Set targets to the customer's contract and review error-budget burn alerts before production use.",
        ),
        control(
            "Quota and chargeback governance",
            exists("governance/quota-plans.yaml", "scripts/quota-check.py", "runbooks/quota-chargeback.md", "results/quota/sample-summary.md")
            and executable("scripts/quota-check.py")
            and len(nested(quota_plans, "spec", "plans", default=[])) >= 3
            and {"platform.ai/owner", "platform.ai/cost-center", "platform.ai/environment", "platform.ai/sandbox-id"}
            <= set(nested(quota_plans, "spec", "chargeback", "requiredLabels", default=[])),
            "Reviewed quota plans connect tenant ResourceQuota, gateway sandbox budgets, workspace sizing, and chargeback labels.",
            ["governance/quota-plans.yaml", "scripts/quota-check.py", "runbooks/quota-chargeback.md"],
            "Align quota plans to customer chargeback policy before tenant onboarding or budget increases.",
        ),
        control(
            "Egress governance for coding agents",
            exists("network/egress-catalog.yaml", "scripts/egress-governance.py", "runbooks/egress-governance.md", "results/egress-governance/sample-summary.md")
            and executable("scripts/egress-governance.py")
            and "customer-git-artifact-mirror-example" in {entry.get("id") for entry in nested(egress_catalog, "spec", "entries", default=[]) if isinstance(entry, dict)}
            and "catalogRef" in read_text("tenants/onboarding/coding-agents.yaml"),
            "External coding-agent egress must reference approved catalog entries before NetworkPolicies allow it.",
            ["network/egress-catalog.yaml", "scripts/egress-governance.py", "runbooks/egress-governance.md"],
            "Review catalog entries before adding Git, package mirror, artifact, or ticketing egress for agents.",
        ),
        control(
            "Data retention and privacy governance",
            exists("governance/data-retention.yaml", "scripts/retention-check.py", "runbooks/data-retention.md", "results/retention/sample-summary.md")
            and executable("scripts/retention-check.py")
            and nested(retention_policy, "spec", "classes", "auditLogs", "storesRawPrompt") is False
            and nested(retention_policy, "spec", "classes", "auditLogs", "storesRawQuery") is False,
            "Retention policy covers redacted audit logs, generated evidence, RAG knowledge, agent workspace data, and model governance records.",
            ["governance/data-retention.yaml", "scripts/retention-check.py", "runbooks/data-retention.md"],
            "Align retention days and classifications to customer policy before long-running use.",
        ),
        control(
            "Advanced chaos drills",
            exists("chaos/drills/gpu-capacity-preflight.yaml", "chaos/drills/qdrant-vector-store-rollout.yaml", "chaos/drills/vllm-runtime-rollout.yaml", "scripts/chaos-drill.sh", "runbooks/chaos-drills.md")
            and {"gpu-capacity-preflight", "qdrant-vector-store-rollout", "vllm-runtime-rollout", "rag-service-rollout"} <= chaos_drill_names
            and "capacity-preflight" in read_text("chaos/drills/gpu-capacity-preflight.yaml")
            and "EXPECTED_RAG_BACKEND=qdrant" in read_text("chaos/drills/qdrant-vector-store-rollout.yaml"),
            "The chaos catalog covers RAG, vector-store, vLLM runtime, and GPU capacity preflight drills in addition to core rollouts.",
            ["chaos/drills/", "scripts/chaos-drill.sh", "runbooks/chaos-drills.md"],
            "Run dependency and GPU-capacity drills in customer clusters during maintenance windows before production handoff.",
        ),
        control(
            "NVIDIA and AMD accelerator profiles",
            nested(vllm_nvidia, "accelerator", "resourceName") == "nvidia.com/gpu"
            and nested(vllm_amd, "accelerator", "resourceName") == "amd.com/gpu"
            and "rocm" in str(nested(vllm_amd, "image", "repository", default="")).lower(),
            "vLLM customer values include NVIDIA CUDA and AMD ROCm scheduling profiles.",
            ["clusters/customer/values/vllm-nvidia.yaml", "clusters/customer/values/vllm-amd.yaml", "runbooks/gpu-capacity.md"],
            "Verify the customer's GPU device plugin resource names and node labels before enabling replicas.",
        ),
        control(
            "Multi-replica runtime availability",
            nested(customer_gateway, "replicaCount", default=0) >= 2
            and nested(customer_gateway, "keda", "enabled") is True
            and nested(vllm_nvidia, "autoscaling", "enabled") is True
            and nested(vllm_amd, "autoscaling", "enabled") is True,
            "Customer profiles demonstrate multiple replicas, autoscaling, PDBs, and topology spread.",
            ["clusters/customer/values/inference-gateway.yaml", "clusters/customer/values/vllm.yaml", "charts/inference-gateway/templates/pdb.yaml"],
            "Tune min/max replicas to customer SLOs, GPU inventory, and maintenance windows.",
        ),
        control(
            "Observability and cost labels",
            exists("observability/alerts/ai-platform-alerts.yaml", "observability/dashboards/inference-dashboard.json")
            and "platform.ai/cost-center" in set(nested(quota_plans, "spec", "chargeback", "requiredLabels", default=[])),
            "Metrics, alerts, dashboards, and cost-label expectations are documented and versioned.",
            ["observability/", "docs/production-readiness.md"],
            "Connect these signals to the customer's Prometheus, logs, dashboard, and chargeback systems.",
        ),
        control(
            "Policy as code",
            exists("policies/kyverno/policies.yaml", "policies/kyverno/tests/kyverno-test.yaml")
            and "verifyImages" in read_text("policies/kyverno/policies.yaml"),
            "Kyverno policies cover labels, resources, pod hardening, image tags, and signature verification.",
            ["policies/kyverno/policies.yaml", "runbooks/policy-blocked-deploy.md"],
            "Run policies in audit mode first, then enforce on agreed AI namespaces.",
        ),
        control(
            "Supply-chain controls",
            "anchore/sbom-action" in workflow
            and "trivy-action" in workflow
            and "actions/attest-build-provenance@v4.1.1" in workflow
            and "actions/attest@v4.1.1" in workflow
            and "steps.build_gateway.outputs.digest" in workflow
            and "steps.build_rag.outputs.digest" in workflow
            and 'exit-code: "1"' in workflow
            and "image-scan:" in read_text("Makefile")
            and "spdx-json" in read_text("scripts/image-scan.sh")
            and "--format sarif" in read_text("scripts/image-scan.sh")
            and "supply-chain-checksums.txt" in workflow,
            "CI builds images, generates SBOMs, fails on high/critical image vulnerabilities, signs immutable image digests, publishes SLSA/SBOM attestations, and uploads supply-chain evidence.",
            [".github/workflows/ci.yml", "scripts/image-scan.sh"],
            "Promote only signed/scanned image digests with downloadable evidence into customer registries.",
        ),
        control(
            "Restore-drill integration",
            exists("backup/restore-drill/drills/local-redis-aof.yaml", "scripts/restore-drill.sh", "results/restore-drill/sample-redis-run.json")
            and "RamazanKara/restore-drill" in read_text("backup/restore-drill/README.md"),
            "Application-data restore verification uses the restore-drill project, with Velero examples kept separate.",
            ["backup/restore-drill/", "results/restore-drill/sample-redis-run.json", "runbooks/restore-drill.md"],
            "Run scheduled drills against each critical customer data store and retain generated reports per policy.",
        ),
        control(
            "Evaluation, load, and incident evidence",
            exists("evals/smoke-suite.yaml", "evals/coding-agent-suite.yaml", "results/evals/sample-summary.md", "results/evals/sample-coding-agent-summary.md", "results/loadtest/sample-summary.md")
            and executable("scripts/eval.sh")
            and executable("scripts/loadtest.sh")
            and executable("scripts/loadtest-local.sh"),
            "The lab stores smoke and coding-agent evaluation summaries alongside load-test, incident, and chaos evidence.",
            ["evals/smoke-suite.yaml", "evals/coding-agent-suite.yaml", "results/evals/sample-summary.md", "results/loadtest/sample-summary.md", "scripts/loadtest-local.sh"],
            "Keep customer-specific evaluation and load results with release evidence.",
        ),
        control(
            "Production validation command",
            executable("scripts/production-check.py") and executable("scripts/validate.sh") and "Evidence pack" in production_doc,
            "`make validate` and `make production-check` include static readiness gates.",
            ["scripts/production-check.py", "scripts/validate.sh", "docs/production-readiness.md"],
            "Run static validation before demos, upgrades, and handoff reviews.",
        ),
    ]


def kubectl_json(args: list[str]) -> tuple[bool, Any, str]:
    if shutil.which("kubectl") is None:
        return False, {}, "kubectl not found"
    completed = subprocess.run(["kubectl", *args, "-o", "json"], text=True, capture_output=True, timeout=20)
    if completed.returncode != 0:
        return False, {}, completed.stderr.strip() or completed.stdout.strip()
    return True, json.loads(completed.stdout), ""


def live_controls() -> list[Control]:
    checks: list[Control] = []
    for namespace in ["inference", "rag", "ai-agents", "ai-sandbox", "budget", "ollama"]:
        ok, _, error = kubectl_json(["get", "namespace", namespace])
        checks.append(
            Control(
                area=f"Live namespace: {namespace}",
                status="pass" if ok else "fail",
                summary=f"Namespace `{namespace}` is reachable." if ok else f"Namespace `{namespace}` is not ready: {error}",
                evidence=[f"kubectl get namespace {namespace}"],
                customer_action="Re-run local sync or inspect cluster access before using this evidence pack.",
            )
        )

    for namespace, name in [
        ("inference", "inference-gateway-inference-gateway"),
        ("rag", "rag-service-rag-service"),
        ("budget", "budget-redis"),
    ]:
        ok, payload, error = kubectl_json(["-n", namespace, "get", "deployment", name])
        available = nested(payload, "status", "availableReplicas", default=0) or 0
        desired = nested(payload, "spec", "replicas", default=0) or 0
        ready = ok and desired > 0 and available >= min(desired, 1)
        checks.append(
            Control(
                area=f"Live deployment: {namespace}/{name}",
                status="pass" if ready else "fail",
                summary=f"Deployment has {available}/{desired} available replicas." if ok else f"Deployment is not reachable: {error}",
                evidence=[f"kubectl -n {namespace} get deployment {name}"],
                customer_action="Check rollout status, image pull, probes, resource quota, and network policy.",
            )
        )

    ok, payload, error = kubectl_json(["-n", "ai-agents", "get", "pvc", "agent-workspace"])
    phase = nested(payload, "status", "phase", default="")
    checks.append(
        Control(
            area="Live agent workspace PVC",
            status="pass" if ok and phase == "Bound" else "fail",
            summary=f"PVC phase is `{phase}`." if ok else f"PVC is not reachable: {error}",
            evidence=["kubectl -n ai-agents get pvc agent-workspace"],
            customer_action="Confirm the storage class and quota support the requested workspace size.",
        )
    )
    return checks


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def write_markdown(path: Path, generated_at: str, controls: list[Control], artifacts: list[Artifact], live: bool) -> None:
    passes = sum(1 for item in controls if item.status == "pass")
    failures = sum(1 for item in controls if item.status == "fail")
    lines = [
        "# Private AI Platform Kit Evidence Pack",
        "",
        f"Generated: `{generated_at}`",
        f"Mode: `{'static-and-live' if live else 'static'}`",
        "",
        f"Summary: {passes} passed, {failures} failed.",
        "",
        "## Control Matrix",
        "",
        "| Area | Status | Summary | Evidence | Customer action |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in controls:
        evidence = ", ".join(f"`{entry}`" for entry in item.evidence)
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_escape(item.area),
                    item.status,
                    markdown_escape(item.summary),
                    markdown_escape(evidence),
                    markdown_escape(item.customer_action),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Evidence Artifacts",
            "",
            "| Name | Path | Kind | Source |",
            "| --- | --- | --- | --- |",
        ]
    )
    for artifact in artifacts:
        lines.append(f"| {markdown_escape(artifact.name)} | `{artifact.path}` | {artifact.kind} | {artifact.source} |")

    lines.extend(
        [
            "",
            "## Recommended Handoff Commands",
            "",
            "Run these before a customer demo, release review, or incident drill handoff:",
            "",
            "    make toolchain-install",
            "    make validate-full",
            "    make toolchain-report TOOLCHAIN_PROFILE=strict",
            "    make slo-report",
            "    make quota-report",
            "    make egress-report",
            "    make retention-report",
            "    make model-provenance-report",
            "    make smoke RUNTIME_BACKEND=ollama",
            "    make rag-smoke",
            "    make agent-smoke",
            "    make eval",
            "    make restore-drill RUNTIME=local",
            "    make evidence LIVE=1",
            "    make release-gate-strict",
            "    make release-report-strict",
            "",
            "Use `make release-gate` only for local configuration checks where checked-in sample evidence is acceptable.",
        ]
    )
    path.write_text("\n".join(lines) + "\n")


def write_json(path: Path, generated_at: str, controls: list[Control], artifacts: list[Artifact], live: bool) -> None:
    payload = {
        "project": "Private AI Platform Kit",
        "generated_at": generated_at,
        "mode": "static-and-live" if live else "static",
        "summary": {
            "passed": sum(1 for item in controls if item.status == "pass"),
            "failed": sum(1 for item in controls if item.status == "fail"),
        },
        "controls": [asdict(item) for item in controls],
        "artifacts": [asdict(item) for item in artifacts],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or validate a Private AI Platform Kit customer evidence pack.")
    parser.add_argument("--output-dir", default="results/evidence")
    parser.add_argument("--check", action="store_true", help="Validate static evidence-pack inputs without writing a report.")
    parser.add_argument("--live", action="store_true", help="Include live Kubernetes readiness checks.")
    args = parser.parse_args()

    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    controls = static_controls()
    if args.live:
        controls.extend(live_controls())
    artifacts = collect_artifacts()
    failed = [item for item in controls if item.status == "fail"]

    if args.check:
        if failed:
            print("evidence pack check failed:")
            for item in failed:
                print(f"- {item.area}: {item.summary}")
            return 1
        print(f"evidence pack checks ok ({len(controls)} controls, {len(artifacts)} artifacts)")
        return 0

    output_dir = ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"evidence-{stamp}.json"
    md_path = output_dir / f"evidence-{stamp}.md"
    write_json(json_path, generated_at, controls, artifacts, args.live)
    write_markdown(md_path, generated_at, controls, artifacts, args.live)
    print(f"wrote {rel(json_path)} and {rel(md_path)}")
    if failed:
        print("evidence pack contains failed controls:")
        for item in failed:
            print(f"- {item.area}: {item.summary}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
