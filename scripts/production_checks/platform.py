from __future__ import annotations

import os
from typing import Any

import yaml

from .common import ROOT, find_kind, load_yaml_documents, nested, render_chart, require


def check_model_catalog(errors: list[str]) -> None:
    catalog_path = ROOT / "platform/model-catalog/models.yaml"
    require(errors, catalog_path.exists(), "model catalog must exist at platform/model-catalog/models.yaml")
    if not catalog_path.exists():
        return
    catalog = yaml.safe_load(catalog_path.read_text())
    models = nested(catalog, "spec", "models", default=[])
    model_ids = {model.get("id") for model in models}
    approved_runtimes = {model.get("runtime") for model in models if model.get("status") == "approved"}
    require(
        errors,
        "ollama" in approved_runtimes,
        "model catalog must include at least one approved ollama model for the local smoke",
    )
    require(
        errors,
        "vllm" in approved_runtimes,
        "model catalog must include at least one approved vllm model for the customer GPU profile",
    )
    for model in models:
        model_id = model.get("id", "<unknown>")
        require(errors, bool(model.get("owner")), f"model catalog entry {model_id} must define owner")
        require(
            errors,
            model.get("status") in {"proposed", "approved", "deprecated", "blocked"},
            f"model catalog entry {model_id} must define a valid lifecycle status",
        )
        require(
            errors,
            isinstance(model.get("contextWindow"), int) and model.get("contextWindow") > 0,
            f"model catalog entry {model_id} must define a positive contextWindow",
        )
    for environment in ("local", "customer"):
        values = yaml.safe_load((ROOT / f"deploy/clusters/{environment}/values/inference-gateway.yaml").read_text())
        for model_id in nested(values, "runtime", "allowedModels", default=[]):
            require(
                errors, model_id in model_ids, f"{environment}: allowed model {model_id} is missing from model catalog"
            )
    configmap = ROOT / "platform/model-catalog/k8s/configmap.yaml"
    require(errors, configmap.exists(), "model catalog ConfigMap must exist")
    if configmap.exists():
        docs = load_yaml_documents(configmap)
        require(errors, bool(find_kind(docs, "ConfigMap")), "model catalog ConfigMap must render as a ConfigMap")


def check_model_governance(errors: list[str]) -> None:
    script = ROOT / "scripts/model-catalog.py"
    require(errors, os.access(script, os.X_OK), "scripts/model-catalog.py must be executable")
    require(errors, (ROOT / "runbooks/model-governance.md").exists(), "model governance runbook must exist")
    require(
        errors, (ROOT / "results/model-catalog/sample-summary.md").exists(), "model catalog sample summary must exist"
    )
    catalog_models = nested(
        yaml.safe_load((ROOT / "platform/model-catalog/models.yaml").read_text()), "spec", "models", default=[]
    )
    for model in catalog_models:
        if model.get("status") == "approved":
            promotion_request = model.get("promotionRequest")
            require(
                errors,
                bool(promotion_request) and (ROOT / str(promotion_request)).exists(),
                f"approved model {model.get('id')} must reference an existing promotion request",
            )


PLATFORM_NAMESPACE_CHARTS = (
    "inference-gateway",
    "rag-service",
    "qdrant-vector-store",
    "vllm",
    "ollama",
    "budget-redis",
)


def check_platform_namespace_psa(errors: list[str]) -> None:
    """Assert every platform data-plane chart renders a namespace enforcing restricted PSA.

    The tenant/sandbox namespaces already carry apiserver-native Pod Security Admission labels;
    this asserts the same restricted floor on the platform service namespaces that handle the
    most sensitive aggregate data, so pod hardening does not depend solely on the Kyverno webhook.
    """
    for chart in PLATFORM_NAMESPACE_CHARTS:
        docs = render_chart(chart)
        namespaces = find_kind(docs, "Namespace")
        require(errors, len(namespaces) >= 1, f"{chart}: chart must render a data-plane Namespace")
        if not namespaces:
            continue
        labels = namespaces[0].get("metadata", {}).get("labels", {})
        require(
            errors,
            labels.get("pod-security.kubernetes.io/enforce") == "restricted",
            f"{chart}: namespace must enforce restricted pod security",
        )
        require(
            errors,
            labels.get("pod-security.kubernetes.io/warn") == "restricted",
            f"{chart}: namespace must set warn=restricted",
        )


def check_sandbox(errors: list[str]) -> None:
    sandbox_docs: list[dict[str, Any]] = []
    for path in sorted((ROOT / "deploy/sandbox/base").glob("*.yaml")):
        sandbox_docs.extend(load_yaml_documents(path))

    namespaces = find_kind(sandbox_docs, "Namespace")
    require(errors, len(namespaces) == 1, "sandbox: expected one Namespace")
    if namespaces:
        labels = namespaces[0].get("metadata", {}).get("labels", {})
        require(
            errors,
            labels.get("platform.ai/traceable-sandbox") == "true",
            "sandbox: namespace must be labeled as traceable",
        )
        require(
            errors,
            labels.get("pod-security.kubernetes.io/enforce") == "restricted",
            "sandbox: namespace must enforce restricted pod security",
        )
        require(
            errors,
            labels.get("platform.ai/sandbox-id") == "local-lab",
            "sandbox: namespace must carry sandbox id label",
        )

    quotas = find_kind(sandbox_docs, "ResourceQuota")
    require(errors, len(quotas) == 1, "sandbox: expected one ResourceQuota")
    if quotas:
        hard = quotas[0].get("spec", {}).get("hard", {})
        for key in ("requests.cpu", "requests.memory", "limits.cpu", "limits.memory", "pods"):
            require(errors, key in hard, f"sandbox: ResourceQuota missing {key}")

    require(errors, bool(find_kind(sandbox_docs, "LimitRange")), "sandbox: expected a LimitRange")
    policies = find_kind(sandbox_docs, "NetworkPolicy")
    require(errors, len(policies) >= 2, "sandbox: expected default-deny and allowlist NetworkPolicies")
    default_deny = next(
        (policy for policy in policies if policy.get("metadata", {}).get("name") == "ai-sandbox-default-deny"), None
    )
    require(errors, default_deny is not None, "sandbox: missing default-deny NetworkPolicy")
    if default_deny:
        require(
            errors,
            "Ingress" in default_deny.get("spec", {}).get("policyTypes", []),
            "sandbox: default-deny must include Ingress",
        )
        require(
            errors,
            "Egress" in default_deny.get("spec", {}).get("policyTypes", []),
            "sandbox: default-deny must include Egress",
        )
        require(
            errors,
            "ingress" not in default_deny.get("spec", {}),
            "sandbox: default-deny must not define ingress allows",
        )
        require(
            errors, "egress" not in default_deny.get("spec", {}), "sandbox: default-deny must not define egress allows"
        )


def check_evals(errors: list[str]) -> None:
    suite_path = ROOT / "platform/evals/smoke-suite.yaml"
    require(errors, suite_path.exists(), "eval smoke suite must exist at platform/evals/smoke-suite.yaml")
    if suite_path.exists():
        suite = yaml.safe_load(suite_path.read_text())
        require(errors, suite.get("kind") == "EvalSuite", "eval smoke suite must use kind EvalSuite")
        cases = nested(suite, "spec", "cases", default=[])
        require(errors, isinstance(cases, list) and len(cases) >= 2, "eval smoke suite must define at least two cases")
        for case in cases:
            case_id = case.get("id", "<unknown>") if isinstance(case, dict) else "<invalid>"
            require(errors, isinstance(case, dict), f"eval case {case_id} must be a mapping")
            if not isinstance(case, dict):
                continue
            require(errors, bool(case.get("messages")), f"eval case {case_id} must define messages")
            require(errors, bool(case.get("checks")), f"eval case {case_id} must define checks")
    require(errors, os.access(ROOT / "scripts/eval.sh", os.X_OK), "scripts/eval.sh must be executable")
    require(errors, os.access(ROOT / "scripts/eval-suite.py", os.X_OK), "scripts/eval-suite.py must be executable")
    require(errors, (ROOT / "results/evals/sample-summary.md").exists(), "eval sample summary must exist")
    coding_agent_suite = ROOT / "platform/evals/coding-agent-suite.yaml"
    require(
        errors,
        coding_agent_suite.exists(),
        "coding-agent eval suite must exist at platform/evals/coding-agent-suite.yaml",
    )
    require(
        errors,
        (ROOT / "results/evals/sample-coding-agent-summary.md").exists(),
        "coding-agent eval sample summary must exist",
    )
    require(
        errors,
        (ROOT / "results/evals/sample-coding-agent-summary.json").exists(),
        "coding-agent eval sample JSON must exist",
    )
    if coding_agent_suite.exists():
        suite = yaml.safe_load(coding_agent_suite.read_text())
        cases = nested(suite, "spec", "cases", default=[])
        require(errors, suite.get("kind") == "EvalSuite", "coding-agent eval suite must use kind EvalSuite")
        require(
            errors,
            isinstance(cases, list) and len(cases) >= 4,
            "coding-agent eval suite must define at least four cases",
        )
        require(
            errors,
            "forbiddenAny" in coding_agent_suite.read_text(),
            "coding-agent eval suite must include forbiddenAny secret-leak checks",
        )


def check_tenant_labs(errors: list[str]) -> None:
    tenant_example = ROOT / "tenants/examples/team-a-lab.yaml"
    require(errors, tenant_example.exists(), "tenant example must exist at tenants/examples/team-a-lab.yaml")
    if tenant_example.exists():
        docs = load_yaml_documents(tenant_example)
        require(errors, bool(find_kind(docs, "Namespace")), "tenant example must define a Namespace")
        require(errors, bool(find_kind(docs, "ResourceQuota")), "tenant example must define a ResourceQuota")
        require(errors, bool(find_kind(docs, "LimitRange")), "tenant example must define a LimitRange")
        require(
            errors,
            len(find_kind(docs, "NetworkPolicy")) >= 2,
            "tenant example must define default-deny and allowlist NetworkPolicies",
        )
        require(errors, bool(find_kind(docs, "ConfigMap")), "tenant example must define a trace contract ConfigMap")
        require(errors, bool(find_kind(docs, "Role")), "tenant example must define a Role")
        require(errors, bool(find_kind(docs, "RoleBinding")), "tenant example must define a RoleBinding")
        configmaps = find_kind(docs, "ConfigMap")
        if configmaps:
            require(
                errors, "rag-url" in configmaps[0].get("data", {}), "tenant example trace contract must publish rag-url"
            )
        namespace = next(iter(find_kind(docs, "Namespace")), {})
        labels = namespace.get("metadata", {}).get("labels", {})
        for key in (
            "platform.ai/tenant",
            "platform.ai/sandbox-id",
            "platform.ai/traceable-sandbox",
            "pod-security.kubernetes.io/enforce",
        ):
            require(errors, key in labels, f"tenant example Namespace missing label {key}")
    require(errors, os.access(ROOT / "scripts/tenant-up.sh", os.X_OK), "scripts/tenant-up.sh must be executable")
    require(errors, os.access(ROOT / "scripts/tenant-smoke.sh", os.X_OK), "scripts/tenant-smoke.sh must be executable")


def check_tenant_onboarding(errors: list[str]) -> None:
    script = ROOT / "scripts/tenant-onboard.py"
    spec = ROOT / "tenants/onboarding/coding-agents.yaml"
    regulated_spec = ROOT / "tenants/onboarding/regulated-offline-coding-agents.yaml"
    require(errors, os.access(script, os.X_OK), "scripts/tenant-onboard.py must be executable")
    require(errors, spec.exists(), "tenant onboarding spec must exist at tenants/onboarding/coding-agents.yaml")
    require(errors, regulated_spec.exists(), "regulated/offline tenant onboarding spec must exist")
    if spec.exists():
        onboarding = yaml.safe_load(spec.read_text())
        require(
            errors,
            nested(onboarding, "spec", "tenant", "namespace") == "ai-coding-agents",
            "tenant onboarding spec should define the coding-agent namespace",
        )
        require(
            errors,
            nested(onboarding, "spec", "agentWorkspace", "enabled") is True,
            "tenant onboarding spec should enable agent workspace output",
        )
        require(
            errors,
            bool(nested(onboarding, "spec", "network", "allowedEgressCidrs", default=[])),
            "tenant onboarding spec should include an approved external egress example",
        )
    if regulated_spec.exists():
        onboarding = yaml.safe_load(regulated_spec.read_text())
        require(
            errors,
            nested(onboarding, "spec", "tenant", "namespace") == "ai-regulated-agents",
            "regulated/offline onboarding spec should define the regulated namespace",
        )
        require(
            errors,
            nested(onboarding, "spec", "compliance", "profile") == "regulated-offline",
            "regulated/offline onboarding spec must set compliance profile",
        )
        require(
            errors,
            nested(onboarding, "spec", "compliance", "externalEgressAllowed") is False,
            "regulated/offline onboarding spec must disallow external egress",
        )
        require(
            errors,
            nested(onboarding, "spec", "network", "allowedEgressCidrs", default=[]) == [],
            "regulated/offline onboarding spec must not include external CIDR egress",
        )
        require(
            errors,
            nested(onboarding, "spec", "agentWorkspace", "rbac", "allowJobManagement") is False,
            "regulated/offline onboarding spec should disable job management by default",
        )


def check_chaos_drills(errors: list[str]) -> None:
    drill_dir = ROOT / "chaos/drills"
    require(errors, drill_dir.exists(), "chaos drill directory must exist")
    expected = {
        "gateway-rollout",
        "budget-redis-rollout",
        "ollama-rollout",
        "rag-service-rollout",
        "qdrant-vector-store-rollout",
        "vllm-runtime-rollout",
        "gpu-capacity-preflight",
    }
    allowed_actions = {"rollout-restart", "capacity-preflight"}
    seen: set[str] = set()
    if drill_dir.exists():
        for path in sorted(drill_dir.glob("*.yaml")):
            docs = load_yaml_documents(path)
            require(errors, len(docs) == 1, f"{path.relative_to(ROOT)} must contain one document")
            if not docs:
                continue
            doc = docs[0]
            require(errors, doc.get("kind") == "ChaosDrill", f"{path.relative_to(ROOT)} must use kind ChaosDrill")
            name = nested(doc, "metadata", "name")
            if name:
                seen.add(name)
            action = nested(doc, "spec", "action")
            require(
                errors,
                action in allowed_actions,
                f"{path.relative_to(ROOT)} action must be one of {sorted(allowed_actions)}",
            )
            require(
                errors, bool(nested(doc, "spec", "target", "name")), f"{path.relative_to(ROOT)} must define target name"
            )
            require(
                errors, bool(nested(doc, "spec", "target", "kind")), f"{path.relative_to(ROOT)} must define target kind"
            )
            if action == "rollout-restart":
                require(
                    errors,
                    bool(nested(doc, "spec", "target", "namespace")),
                    f"{path.relative_to(ROOT)} rollout drill must define target namespace",
                )
            require(
                errors,
                bool(nested(doc, "spec", "validation", "command")),
                f"{path.relative_to(ROOT)} must define validation command",
            )
            require(
                errors,
                nested(doc, "spec", "safety", "destructive") is False,
                f"{path.relative_to(ROOT)} must be marked non-destructive",
            )
            require(
                errors,
                isinstance(nested(doc, "spec", "safety", "maxDurationSeconds"), int),
                f"{path.relative_to(ROOT)} must define safety.maxDurationSeconds",
            )
    require(errors, expected <= seen, f"chaos drills missing expected definitions: {sorted(expected - seen)}")
    require(errors, os.access(ROOT / "scripts/chaos-drill.sh", os.X_OK), "scripts/chaos-drill.sh must be executable")
    if (ROOT / "scripts/chaos-drill.sh").exists():
        source = (ROOT / "scripts/chaos-drill.sh").read_text()
        for phrase in (
            "gpu-capacity-preflight",
            "qdrant-vector-store-rollout",
            "vllm-runtime-rollout",
            "rag-service-rollout",
        ):
            require(errors, phrase in source, f"chaos runner missing {phrase}")


def check_static_workload_security(errors: list[str]) -> None:
    restore_cronjobs = find_kind(load_yaml_documents(ROOT / "deploy/backup/restore-drill/k8s/cronjob.yaml"), "CronJob")
    require(errors, len(restore_cronjobs) == 1, "restore-drill CronJob manifest must define one CronJob")
    if restore_cronjobs:
        pod_spec = nested(restore_cronjobs[0], "spec", "jobTemplate", "spec", "template", "spec", default={})
        containers = pod_spec.get("containers", [])
        require(errors, len(containers) == 1, "restore-drill CronJob must define one container")
        if containers:
            restore = containers[0]
            security = restore.get("securityContext", {})
            mounts = {mount.get("name"): mount for mount in restore.get("volumeMounts", [])}
            require(
                errors,
                security.get("allowPrivilegeEscalation") is False,
                "restore-drill CronJob must block privilege escalation",
            )
            require(
                errors,
                security.get("readOnlyRootFilesystem") is True,
                "restore-drill CronJob must use a read-only root filesystem",
            )
            require(
                errors,
                "ALL" in nested(security, "capabilities", "drop", default=[]),
                "restore-drill CronJob must drop all Linux capabilities",
            )
            require(
                errors, nested(mounts, "config", "readOnly") is True, "restore-drill config mount must be read-only"
            )
            require(
                errors, nested(mounts, "fixtures", "readOnly") is True, "restore-drill fixtures mount must be read-only"
            )
            require(errors, "reports" in mounts, "restore-drill CronJob must mount writable reports storage")
            require(
                errors,
                "tmp" in mounts and mounts["tmp"].get("mountPath") == "/tmp",
                "restore-drill CronJob must mount writable tmp storage",
            )
        volumes = {volume.get("name") for volume in pod_spec.get("volumes", [])}
        require(
            errors,
            {"config", "reports", "fixtures", "tmp"} <= volumes,
            "restore-drill CronJob must define config, reports, fixtures, and tmp volumes",
        )

    restore_rbac = (ROOT / "deploy/backup/restore-drill/k8s/rbac.yaml").read_text()
    require(errors, "pods/exec" not in restore_rbac, "restore-drill RBAC must not grant pods/exec")

    smoke_jobs = find_kind(load_yaml_documents(ROOT / "deploy/sandbox/tests/trace-smoke-job.yaml"), "Job")
    require(errors, len(smoke_jobs) == 1, "sandbox trace smoke manifest must define one Job")
    if smoke_jobs:
        pod_spec = nested(smoke_jobs[0], "spec", "template", "spec", default={})
        containers = pod_spec.get("containers", [])
        require(errors, len(containers) == 1, "sandbox trace smoke Job must define one container")
        if containers:
            smoke = containers[0]
            security = smoke.get("securityContext", {})
            mounts = {mount.get("name"): mount for mount in smoke.get("volumeMounts", [])}
            require(
                errors,
                security.get("allowPrivilegeEscalation") is False,
                "sandbox trace smoke Job must block privilege escalation",
            )
            require(
                errors,
                security.get("readOnlyRootFilesystem") is True,
                "sandbox trace smoke Job must use a read-only root filesystem",
            )
            require(
                errors,
                "ALL" in nested(security, "capabilities", "drop", default=[]),
                "sandbox trace smoke Job must drop all Linux capabilities",
            )
            require(
                errors,
                "tmp" in mounts and mounts["tmp"].get("mountPath") == "/tmp",
                "sandbox trace smoke Job must mount writable tmp storage",
            )
        volumes = {volume.get("name") for volume in pod_spec.get("volumes", [])}
        require(errors, "tmp" in volumes, "sandbox trace smoke Job must define a tmp volume")

    policies = (ROOT / "deploy/policies/kyverno/policies.yaml").read_text()
    require(
        errors,
        "require-read-only-root-filesystem" in policies,
        "Kyverno restricted policy must require read-only root filesystems",
    )
    require(
        errors,
        "deny-unscoped-egress-rules" in policies,
        "Kyverno egress policy must reject rules that omit destinations",
    )
    kyverno_tests = (ROOT / "deploy/policies/kyverno/tests/kyverno-test.yaml").read_text()
    require(
        errors, "writable-root-pod" in kyverno_tests, "Kyverno tests must cover read-only root filesystem enforcement"
    )
    require(
        errors,
        "unscoped-egress" in kyverno_tests and "customer-model-pull-exception" in kyverno_tests,
        "Kyverno tests must cover unscoped egress and customer misuse of the local exception",
    )


def check_gitops_revisions(errors: list[str]) -> None:
    """Require reproducible GitOps refs and the customer embedding dependency."""
    paths = (
        ROOT / "deploy/gitops/argocd/root-app.yaml",
        ROOT / "deploy/gitops/argocd/root-app-customer.yaml",
        ROOT / "deploy/clusters/local/apps.yaml",
        ROOT / "deploy/clusters/customer/apps.yaml",
    )
    for path in paths:
        source = path.read_text()
        require(errors, "targetRevision: HEAD" not in source, f"{path.relative_to(ROOT)} must not track HEAD")
    customer_apps = (ROOT / "deploy/clusters/customer/apps.yaml").read_text()
    require(
        errors,
        "name: runtime-vllm-embeddings" in customer_apps and "values/vllm-embeddings.yaml" in customer_apps,
        "customer GitOps must deploy the embedding runtime referenced by RAG values",
    )


def check_evidence_pack(errors: list[str]) -> None:
    require(
        errors, os.access(ROOT / "scripts/evidence-pack.py", os.X_OK), "scripts/evidence-pack.py must be executable"
    )
    require(errors, (ROOT / "results/evidence/sample-summary.md").exists(), "evidence pack sample summary must exist")
    require(errors, (ROOT / "runbooks/evidence-pack.md").exists(), "evidence pack runbook must exist")
    script = ROOT / "scripts/evidence-pack.py"
    if script.exists():
        source = script.read_text()
        for token in ("class Control", "def static_controls", "def write_markdown", "--live"):
            require(errors, token in source, f"evidence pack script missing expected implementation hook {token}")


def check_validation_toolchain(errors: list[str]) -> None:
    manifest_path = ROOT / "platform/tools/validation-toolchain.yaml"
    script = ROOT / "scripts/toolchain-doctor.py"
    installer = ROOT / "scripts/install-validation-tools.sh"
    require(errors, manifest_path.exists(), "validation toolchain manifest must exist")
    require(errors, os.access(script, os.X_OK), "scripts/toolchain-doctor.py must be executable")
    require(errors, os.access(installer, os.X_OK), "scripts/install-validation-tools.sh must be executable")
    require(errors, (ROOT / "runbooks/validation-toolchain.md").exists(), "validation toolchain runbook must exist")
    require(
        errors,
        (ROOT / "results/toolchain/sample-summary.md").exists(),
        "validation toolchain sample summary must exist",
    )
    if manifest_path.exists():
        manifest = yaml.safe_load(manifest_path.read_text()) or {}
        require(
            errors,
            manifest.get("kind") == "ValidationToolchain",
            "validation toolchain manifest kind must be ValidationToolchain",
        )
        strict_required = set(nested(manifest, "spec", "profiles", "strict", "required", default=[]))
        expected = {
            "python3",
            "helm",
            "kubeconform",
            "kyverno",
            "restore-drill",
            "k6",
            "syft",
            "argocd",
            "cosign",
            "trivy",
        }
        require(
            errors,
            expected <= strict_required,
            f"validation toolchain strict profile missing {sorted(expected - strict_required)}",
        )
        tools = nested(manifest, "spec", "tools", default={})
        for tool in expected - {"python3", "helm"}:
            require(
                errors,
                bool(nested(tools, tool, "defaultVersion")),
                f"validation toolchain {tool} must define defaultVersion",
            )
            require(
                errors,
                bool(nested(tools, tool, "installerEnv")),
                f"validation toolchain {tool} must define installerEnv",
            )
    if installer.exists():
        installer_source = installer.read_text()
        for phrase in ("sha256sum", "KUBECONFORM_VERSION", "KYVERNO_VERSION", "TRIVY_VERSION", "COSIGN_VERSION"):
            require(errors, phrase in installer_source, f"validation tool installer missing {phrase}")
