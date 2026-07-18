from __future__ import annotations

import os
from urllib.parse import urlparse

import yaml

from .common import ROOT, nested, require


def check_slo_governance(errors: list[str]) -> None:
    config_path = ROOT / "platform/slo/objectives.yaml"
    script = ROOT / "scripts/slo-report.py"
    require(errors, config_path.exists(), "SLO objective config must exist at platform/slo/objectives.yaml")
    require(errors, os.access(script, os.X_OK), "scripts/slo-report.py must be executable")
    require(errors, (ROOT / "runbooks/slo-error-budget.md").exists(), "SLO error budget runbook must exist")
    require(errors, (ROOT / "results/slo/sample-summary.md").exists(), "SLO sample summary must exist")
    require(errors, (ROOT / "results/slo/sample-summary.json").exists(), "SLO sample JSON must exist")
    if config_path.exists():
        config = yaml.safe_load(config_path.read_text()) or {}
        require(errors, config.get("kind") == "SLOSet", "SLO objective config kind must be SLOSet")
        objectives = nested(config, "spec", "objectives", default=[])
        objective_ids = {item.get("id") for item in objectives if isinstance(item, dict)}
        expected = {
            "inference-availability",
            "inference-latency",
            "eval-quality-smoke",
            "restore-verification",
            "agent-platform-readiness",
        }
        require(errors, expected <= objective_ids, f"SLO objective config missing {sorted(expected - objective_ids)}")
    alerts = (ROOT / "deploy/observability/alerts/ai-platform-alerts.yaml").read_text()
    for alert in (
        "InferenceGatewayErrorBudgetFastBurn",
        "InferenceGatewayErrorBudgetSlowBurn",
        "InferenceGatewayHighLatency",
        "RestoreDrillFailed",
    ):
        require(errors, alert in alerts, f"SLO alert coverage missing {alert}")


def check_quota_governance(errors: list[str]) -> None:
    policy_path = ROOT / "platform/governance/quota-plans.yaml"
    script = ROOT / "scripts/quota-check.py"
    require(errors, policy_path.exists(), "quota plan policy must exist at platform/governance/quota-plans.yaml")
    require(errors, os.access(script, os.X_OK), "scripts/quota-check.py must be executable")
    require(errors, (ROOT / "runbooks/quota-chargeback.md").exists(), "quota chargeback runbook must exist")
    require(errors, (ROOT / "results/quota/sample-summary.md").exists(), "quota sample summary must exist")
    require(errors, (ROOT / "results/quota/sample-summary.json").exists(), "quota sample JSON must exist")
    if policy_path.exists():
        policy = yaml.safe_load(policy_path.read_text()) or {}
        require(errors, policy.get("kind") == "QuotaPlanSet", "quota plan policy kind must be QuotaPlanSet")
        plans = nested(policy, "spec", "plans", default=[])
        plan_ids = {item.get("id") for item in plans if isinstance(item, dict)}
        expected = {"local-lab", "coding-agents-lab", "customer-shared-runtime"}
        require(errors, expected <= plan_ids, f"quota plan policy missing {sorted(expected - plan_ids)}")
        labels = set(nested(policy, "spec", "chargeback", "requiredLabels", default=[]))
        required_labels = {
            "platform.ai/owner",
            "platform.ai/cost-center",
            "platform.ai/environment",
            "platform.ai/sandbox-id",
        }
        require(
            errors,
            required_labels <= labels,
            f"quota plan policy missing chargeback labels {sorted(required_labels - labels)}",
        )


def check_model_provenance_governance(errors: list[str]) -> None:
    policy_path = ROOT / "platform/governance/model-provenance.yaml"
    script = ROOT / "scripts/model-provenance.py"
    require(
        errors, policy_path.exists(), "model provenance policy must exist at platform/governance/model-provenance.yaml"
    )
    require(errors, os.access(script, os.X_OK), "scripts/model-provenance.py must be executable")
    require(errors, (ROOT / "runbooks/model-provenance.md").exists(), "model provenance runbook must exist")
    require(
        errors,
        (ROOT / "results/model-provenance/sample-summary.md").exists(),
        "model provenance sample summary must exist",
    )
    require(
        errors,
        (ROOT / "results/model-provenance/sample-summary.json").exists(),
        "model provenance sample JSON must exist",
    )
    if policy_path.exists():
        policy = yaml.safe_load(policy_path.read_text()) or {}
        require(
            errors,
            policy.get("kind") == "ModelProvenanceSet",
            "model provenance policy kind must be ModelProvenanceSet",
        )
        artifacts = nested(policy, "spec", "artifacts", default=[])
        model_ids = {item.get("modelId") for item in artifacts if isinstance(item, dict)}
        catalog_models = nested(
            yaml.safe_load((ROOT / "platform/model-catalog/models.yaml").read_text()), "spec", "models", default=[]
        )
        expected = {model.get("id") for model in catalog_models if model.get("status") == "approved"}
        require(
            errors,
            expected <= model_ids,
            f"model provenance must cover all approved catalog models; missing {sorted(expected - model_ids)}",
        )
        required = set(nested(policy, "spec", "requiredEvidence", default=[]))
        required_fields = {
            "sourceUri",
            "immutableRef",
            "digest",
            "license",
            "dataClassification",
            "riskTier",
            "promotionRequest",
            "servingProfiles",
        }
        require(
            errors,
            required_fields <= required,
            f"model provenance policy missing required evidence {sorted(required_fields - required)}",
        )


def check_egress_governance(errors: list[str]) -> None:
    catalog_path = ROOT / "platform/network/egress-catalog.yaml"
    script = ROOT / "scripts/egress-governance.py"
    require(
        errors, catalog_path.exists(), "egress governance catalog must exist at platform/network/egress-catalog.yaml"
    )
    require(errors, os.access(script, os.X_OK), "scripts/egress-governance.py must be executable")
    require(errors, (ROOT / "runbooks/egress-governance.md").exists(), "egress governance runbook must exist")
    require(
        errors,
        (ROOT / "results/egress-governance/sample-summary.md").exists(),
        "egress governance sample summary must exist",
    )
    require(
        errors,
        (ROOT / "results/egress-governance/sample-summary.json").exists(),
        "egress governance sample JSON must exist",
    )
    if catalog_path.exists():
        catalog = yaml.safe_load(catalog_path.read_text()) or {}
        require(
            errors,
            catalog.get("kind") == "ApprovedEgressCatalog",
            "egress governance catalog kind must be ApprovedEgressCatalog",
        )
        entries = nested(catalog, "spec", "entries", default=[])
        require(errors, isinstance(entries, list) and bool(entries), "egress governance catalog must define entries")
        require(
            errors,
            "customer-git-artifact-mirror-example" in {entry.get("id") for entry in entries if isinstance(entry, dict)},
            "egress governance catalog must include customer mirror example",
        )
    onboarding = yaml.safe_load((ROOT / "tenants/onboarding/coding-agents.yaml").read_text())
    for item in nested(onboarding, "spec", "network", "allowedEgressCidrs", default=[]):
        require(errors, bool(item.get("catalogRef")), "tenant onboarding external egress must include catalogRef")


def check_retention_governance(errors: list[str]) -> None:
    policy_path = ROOT / "platform/governance/data-retention.yaml"
    script = ROOT / "scripts/retention-check.py"
    require(errors, policy_path.exists(), "data retention policy must exist at platform/governance/data-retention.yaml")
    require(errors, os.access(script, os.X_OK), "scripts/retention-check.py must be executable")
    require(errors, (ROOT / "runbooks/data-retention.md").exists(), "data retention runbook must exist")
    require(errors, (ROOT / "results/retention/sample-summary.md").exists(), "data retention sample summary must exist")
    require(errors, (ROOT / "results/retention/sample-summary.json").exists(), "data retention sample JSON must exist")
    if policy_path.exists():
        policy = yaml.safe_load(policy_path.read_text()) or {}
        require(
            errors,
            policy.get("kind") == "DataRetentionPolicy",
            "data retention policy kind must be DataRetentionPolicy",
        )
        classes = set(nested(policy, "spec", "classes", default={}))
        expected = {"auditLogs", "generatedEvidence", "ragKnowledge", "agentWorkspace", "modelGovernance"}
        require(errors, expected <= classes, f"data retention policy missing {sorted(expected - classes)}")
        audit = nested(policy, "spec", "classes", "auditLogs", default={})
        require(errors, audit.get("storesRawPrompt") is False, "data retention policy must disallow raw prompts")
        require(errors, audit.get("storesRawQuery") is False, "data retention policy must disallow raw RAG queries")


def check_values_and_docs(errors: list[str]) -> None:
    for environment in ("local", "customer"):
        values = yaml.safe_load((ROOT / f"deploy/clusters/{environment}/values/inference-gateway.yaml").read_text())
        allowed = nested(values, "runtime", "allowedModels", default=[])
        require(errors, bool(allowed), f"{environment}: inference gateway values must define runtime.allowedModels")
        admission = values.get("admission", {})
        for key in ("maxMessages", "maxPromptChars", "maxCompletionTokens", "allowStreaming"):
            require(errors, key in admission, f"{environment}: inference gateway values must define admission.{key}")
        guardrails = values.get("guardrails", {})
        prompt_secret_detection = nested(guardrails, "promptSecretDetection", default={})
        require(
            errors,
            prompt_secret_detection.get("enabled") is True,
            f"{environment}: prompt secret detection should be enabled",
        )
        patterns = prompt_secret_detection.get("patterns", [])
        require(
            errors,
            isinstance(patterns, list) and bool(patterns),
            f"{environment}: prompt secret detection patterns must be set",
        )
        require(errors, "private_key" in patterns, f"{environment}: prompt secret detection should include private_key")
        require(
            errors,
            "generic_api_key_assignment" in patterns,
            f"{environment}: prompt secret detection should include generic_api_key_assignment",
        )
        budget = values.get("budget", {})
        for key in (
            "enabled",
            "backend",
            "requestLimit",
            "promptCharLimit",
            "estimatedTokenLimit",
            "estimatedCharsPerToken",
            "windowSeconds",
            "redisUrl",
            "redisTimeoutSeconds",
            "keyPrefix",
        ):
            require(errors, key in budget, f"{environment}: inference gateway values must define budget.{key}")
        require(errors, budget.get("enabled") is True, f"{environment}: sandbox budgets should be enabled")
        require(
            errors,
            budget.get("backend") == "redis",
            f"{environment}: sandbox budget backend should be redis for shared multi-replica accounting",
        )
        require(
            errors,
            str(budget.get("redisUrl", "")).startswith("redis://"),
            f"{environment}: budget.redisUrl must be a Redis URL",
        )
        for key in (
            "requestLimit",
            "promptCharLimit",
            "estimatedTokenLimit",
            "estimatedCharsPerToken",
            "windowSeconds",
        ):
            require(
                errors,
                isinstance(budget.get(key), int) and budget.get(key) > 0,
                f"{environment}: budget.{key} must be a positive integer",
            )
        auth = values.get("auth", {})
        require(errors, auth.get("enabled") is True, f"{environment}: inference gateway auth.enabled should be true")
        require(
            errors, bool(auth.get("apiKeyHeader")), f"{environment}: inference gateway auth.apiKeyHeader must be set"
        )
        if environment == "local":
            require(
                errors,
                bool(auth.get("apiKeyHashes")),
                f"{environment}: inference gateway auth.apiKeyHashes must be set for local smoke tests",
            )
        else:
            require(
                errors,
                bool(nested(auth, "existingSecret", "name")),
                f"{environment}: inference gateway auth existingSecret.name must be set",
            )
            require(
                errors,
                bool(nested(auth, "existingSecret", "key")),
                f"{environment}: inference gateway auth existingSecret.key must be set",
            )
        rag_values_path = ROOT / f"deploy/clusters/{environment}/values/rag-service.yaml"
        qdrant_values_path = ROOT / f"deploy/clusters/{environment}/values/qdrant-vector-store.yaml"
        agent_values_path = ROOT / f"deploy/clusters/{environment}/values/agent-workspace.yaml"
        require(errors, rag_values_path.exists(), f"{environment}: RAG service values must exist")
        require(errors, qdrant_values_path.exists(), f"{environment}: Qdrant vector-store values must exist")
        require(errors, agent_values_path.exists(), f"{environment}: agent workspace values must exist")
        if rag_values_path.exists():
            rag_values = yaml.safe_load(rag_values_path.read_text())
            require(
                errors,
                nested(rag_values, "traceability", "defaultSandboxId") is not None,
                f"{environment}: RAG values must define traceability.defaultSandboxId",
            )
            expected_backend = "lexical" if environment == "local" else "qdrant"
            require(
                errors,
                nested(rag_values, "retrieval", "backend") == expected_backend,
                f"{environment}: RAG retrieval.backend should be {expected_backend}",
            )
            require(
                errors,
                nested(rag_values, "retrieval", "vectorStore", "collection") is not None,
                f"{environment}: RAG vectorStore.collection must be set",
            )
            require(
                errors,
                nested(rag_values, "retrieval", "vectorStore", "collectionVersion") is not None,
                f"{environment}: RAG vectorStore.collectionVersion must be set",
            )
            require(
                errors,
                nested(rag_values, "retrieval", "vectorStore", "dimensions", default=0) > 0,
                f"{environment}: RAG vectorStore.dimensions must be positive",
            )
            if environment == "customer":
                require(
                    errors,
                    urlparse(str(nested(rag_values, "retrieval", "vectorStore", "url", default=""))).hostname
                    == "qdrant-vector-store.vector.svc.cluster.local",
                    f"{environment}: RAG vectorStore.url should point at the vector namespace service",
                )
            rag_auth = rag_values.get("auth", {})
            require(errors, rag_auth.get("enabled") is True, f"{environment}: RAG auth.enabled should be true")
            require(errors, bool(rag_auth.get("apiKeyHeader")), f"{environment}: RAG auth.apiKeyHeader must be set")
            if environment == "local":
                require(
                    errors,
                    bool(rag_auth.get("apiKeyHashes")),
                    f"{environment}: RAG auth.apiKeyHashes must be set for local smoke tests",
                )
            else:
                require(
                    errors,
                    bool(nested(rag_auth, "existingSecret", "name")),
                    f"{environment}: RAG auth existingSecret.name must be set",
                )
                require(
                    errors,
                    bool(nested(rag_auth, "existingSecret", "key")),
                    f"{environment}: RAG auth existingSecret.key must be set",
                )
        if qdrant_values_path.exists():
            qdrant_values = yaml.safe_load(qdrant_values_path.read_text())
            require(
                errors,
                nested(qdrant_values, "resources", "requests", "memory") is not None,
                f"{environment}: Qdrant values must set memory requests",
            )
            require(
                errors,
                nested(qdrant_values, "resources", "limits", "memory") is not None,
                f"{environment}: Qdrant values must set memory limits",
            )
            if environment == "customer":
                require(
                    errors,
                    nested(qdrant_values, "persistence", "enabled") is True,
                    f"{environment}: Qdrant persistence should be enabled",
                )
                require(
                    errors,
                    bool(nested(qdrant_values, "persistence", "size")),
                    f"{environment}: Qdrant persistence size must be set",
                )
        if agent_values_path.exists():
            agent_values = yaml.safe_load(agent_values_path.read_text())
            require(
                errors,
                nested(agent_values, "sandbox", "id") is not None,
                f"{environment}: agent workspace values must define sandbox.id",
            )

    required_docs = [
        ROOT / "docs/production-readiness.md",
        ROOT / "runbooks/api-access.md",
        ROOT / "runbooks/traceability-sandbox.md",
        ROOT / "runbooks/budget-controls.md",
        ROOT / "runbooks/evaluation-harness.md",
        ROOT / "runbooks/guardrails.md",
        ROOT / "runbooks/model-governance.md",
        ROOT / "runbooks/model-provenance.md",
        ROOT / "runbooks/validation-toolchain.md",
        ROOT / "runbooks/release-gates.md",
        ROOT / "runbooks/slo-error-budget.md",
        ROOT / "runbooks/quota-chargeback.md",
        ROOT / "runbooks/egress-governance.md",
        ROOT / "runbooks/data-retention.md",
        ROOT / "runbooks/tenant-labs.md",
        ROOT / "runbooks/rag-service.md",
        ROOT / "runbooks/vector-rag.md",
        ROOT / "runbooks/agent-workspaces.md",
        ROOT / "runbooks/evidence-pack.md",
        ROOT / "runbooks/chaos-drills.md",
        ROOT / "runbooks/incident-inference-runtime.md",
    ]
    for path in required_docs:
        require(errors, path.exists(), f"missing required production document {path.relative_to(ROOT)}")
    policies = (ROOT / "deploy/policies/kyverno/policies.yaml").read_text()
    require(errors, "platform.ai/sandbox-id" in policies, "Kyverno policies must require sandbox id labels")
    require(errors, os.access(ROOT / "scripts/trace-smoke.sh", os.X_OK), "scripts/trace-smoke.sh must be executable")
    require(errors, os.access(ROOT / "scripts/rag-smoke.sh", os.X_OK), "scripts/rag-smoke.sh must be executable")
    require(errors, os.access(ROOT / "scripts/agent-smoke.sh", os.X_OK), "scripts/agent-smoke.sh must be executable")
    require(
        errors,
        os.access(ROOT / "scripts/agent-sandbox-install.sh", os.X_OK),
        "scripts/agent-sandbox-install.sh must be executable",
    )
    require(
        errors,
        os.access(ROOT / "scripts/agent-sandbox-smoke.sh", os.X_OK),
        "scripts/agent-sandbox-smoke.sh must be executable",
    )
    require(
        errors, os.access(ROOT / "scripts/loadtest-local.sh", os.X_OK), "scripts/loadtest-local.sh must be executable"
    )
    require(errors, (ROOT / "loadtest/mock-runtime.py").exists(), "local load test mock runtime must exist")
    loadtest = (ROOT / "loadtest/chat-completions.js").read_text()
    require(
        errors, "summaryTrendStats" in loadtest and "p(99)" in loadtest, "load test must export p99 latency evidence"
    )
    loadtest_local = (ROOT / "scripts/loadtest-local.sh").read_text()
    for phrase in (
        "loadtest/mock-runtime.py",
        "uvicorn app.main:app",
        "API_KEY_AUTH_ENABLED=true",
        "k6 run --summary-export",
    ):
        require(errors, phrase in loadtest_local, f"local load test harness missing {phrase}")
