from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_yaml_documents(path: Path) -> list[dict[str, Any]]:
    docs = []
    for item in yaml.safe_load_all(path.read_text()):
        if isinstance(item, dict):
            docs.append(item)
    return docs


def render_chart(chart: str, values: Path | None = None) -> list[dict[str, Any]]:
    cmd = ["helm", "template", "production-check", str(ROOT / f"charts/{chart}")]
    if values:
        cmd.extend(["--values", str(values)])
    rendered = subprocess.run(cmd, check=True, text=True, capture_output=True)
    return [doc for doc in yaml.safe_load_all(rendered.stdout) if isinstance(doc, dict)]


def find_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [doc for doc in docs if doc.get("kind") == kind]


def env_names(deployment: dict[str, Any]) -> set[str]:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    env = containers[0].get("env", [])
    return {item.get("name") for item in env}


def env_item(deployment: dict[str, Any], name: str) -> dict[str, Any] | None:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    for item in containers[0].get("env", []):
        if item.get("name") == name:
            return item
    return None


def container(deployment: dict[str, Any]) -> dict[str, Any]:
    return deployment["spec"]["template"]["spec"]["containers"][0]


def nested(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return current if current is not None else default


def require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def check_gateway_render(name: str, docs: list[dict[str, Any]], errors: list[str]) -> None:
    deployments = find_kind(docs, "Deployment")
    require(errors, len(deployments) == 1, f"{name}: expected one gateway Deployment")
    if not deployments:
        return
    deployment = deployments[0]
    pod_spec = deployment["spec"]["template"]["spec"]
    gateway = container(deployment)
    gateway_security = gateway.get("securityContext", {})
    required_env = {
        "RUNTIME_BACKEND",
        "MODEL_ID",
        "OLLAMA_BASE_URL",
        "VLLM_BASE_URL",
        "REQUEST_TIMEOUT_SECONDS",
        "ALLOWED_MODELS",
        "MAX_MESSAGES",
        "MAX_PROMPT_CHARS",
        "MAX_COMPLETION_TOKENS",
        "ALLOW_STREAMING",
        "PROMPT_SECRET_DETECTION_ENABLED",
        "PROMPT_SECRET_PATTERNS",
        "SANDBOX_BUDGET_ENABLED",
        "SANDBOX_BUDGET_BACKEND",
        "SANDBOX_REQUEST_BUDGET",
        "SANDBOX_PROMPT_CHAR_BUDGET",
        "SANDBOX_ESTIMATED_TOKEN_BUDGET",
        "BUDGET_ESTIMATED_CHARS_PER_TOKEN",
        "SANDBOX_BUDGET_WINDOW_SECONDS",
        "SANDBOX_BUDGET_REDIS_URL",
        "SANDBOX_BUDGET_REDIS_TIMEOUT_SECONDS",
        "SANDBOX_BUDGET_KEY_PREFIX",
        "AUDIT_LOG_ENABLED",
        "DEFAULT_SANDBOX_ID",
        "API_KEY_AUTH_ENABLED",
        "API_KEY_HEADER",
        "API_KEY_SHA256S",
    }
    missing_env = required_env - env_names(deployment)

    require(errors, pod_spec.get("automountServiceAccountToken") is False, f"{name}: gateway pod must disable service account token automount")
    require(errors, nested(pod_spec, "securityContext", "runAsNonRoot") is True, f"{name}: gateway pod must run as non-root")
    require(errors, "topologySpreadConstraints" in pod_spec, f"{name}: gateway should render topology spread constraints for multi-replica placement")
    require(errors, not missing_env, f"{name}: gateway Deployment is missing env vars: {sorted(missing_env)}")
    require(errors, gateway_security.get("allowPrivilegeEscalation") is False, f"{name}: gateway must block privilege escalation")
    require(errors, gateway_security.get("readOnlyRootFilesystem") is True, f"{name}: gateway must use a read-only root filesystem")
    require(errors, "ALL" in nested(gateway_security, "capabilities", "drop", default=[]), f"{name}: gateway must drop all Linux capabilities")
    require(errors, bool(nested(gateway, "resources", "requests")), f"{name}: gateway must set resource requests")
    require(errors, bool(nested(gateway, "resources", "limits")), f"{name}: gateway must set resource limits")
    require(errors, bool(find_kind(docs, "ServiceAccount")), f"{name}: gateway chart must render a ServiceAccount")
    service_accounts = find_kind(docs, "ServiceAccount")
    if service_accounts:
        require(errors, service_accounts[0].get("automountServiceAccountToken") is False, f"{name}: ServiceAccount must disable token automount")
    require(errors, bool(find_kind(docs, "NetworkPolicy")), f"{name}: gateway chart must render a NetworkPolicy")
    require(errors, bool(find_kind(docs, "PodDisruptionBudget")), f"{name}: gateway chart must render a PodDisruptionBudget")


def check_budget_redis_render(docs: list[dict[str, Any]], errors: list[str]) -> None:
    deployments = find_kind(docs, "Deployment")
    require(errors, len(deployments) == 1, "budget-redis: expected one Deployment")
    if deployments:
        deployment = deployments[0]
        pod_spec = deployment["spec"]["template"]["spec"]
        redis = container(deployment)
        security = redis.get("securityContext", {})
        image = redis.get("image", "")
        require(errors, ":latest" not in image, "budget-redis: image tag must be pinned")
        require(errors, pod_spec.get("automountServiceAccountToken") is False, "budget-redis: pod must disable service account token automount")
        require(errors, nested(pod_spec, "securityContext", "runAsNonRoot") is True, "budget-redis: pod must run as non-root")
        require(errors, security.get("allowPrivilegeEscalation") is False, "budget-redis: must block privilege escalation")
        require(errors, security.get("readOnlyRootFilesystem") is True, "budget-redis: must use a read-only root filesystem")
        require(errors, "ALL" in nested(security, "capabilities", "drop", default=[]), "budget-redis: must drop all Linux capabilities")
        require(errors, bool(nested(redis, "resources", "requests")), "budget-redis: must set resource requests")
        require(errors, bool(nested(redis, "resources", "limits")), "budget-redis: must set resource limits")
    require(errors, bool(find_kind(docs, "Service")), "budget-redis: chart must render a Service")
    require(errors, bool(find_kind(docs, "ServiceAccount")), "budget-redis: chart must render a ServiceAccount")
    require(errors, len(find_kind(docs, "NetworkPolicy")) >= 2, "budget-redis: chart must render default-deny and gateway allow NetworkPolicies")
    require(errors, bool(find_kind(docs, "PodDisruptionBudget")), "budget-redis: chart must render a PodDisruptionBudget")


def check_qdrant_render(name: str, docs: list[dict[str, Any]], expect_pvc: bool, errors: list[str]) -> None:
    deployments = find_kind(docs, "Deployment")
    require(errors, len(deployments) == 1, f"{name}: expected one Qdrant Deployment")
    if deployments:
        deployment = deployments[0]
        pod_spec = deployment["spec"]["template"]["spec"]
        qdrant = container(deployment)
        security = qdrant.get("securityContext", {})
        image = qdrant.get("image", "")
        require(errors, ":latest" not in image, f"{name}: Qdrant image tag must be pinned")
        require(errors, pod_spec.get("automountServiceAccountToken") is False, f"{name}: Qdrant pod must disable service account token automount")
        require(errors, nested(pod_spec, "securityContext", "runAsNonRoot") is True, f"{name}: Qdrant pod must run as non-root")
        require(errors, security.get("allowPrivilegeEscalation") is False, f"{name}: Qdrant must block privilege escalation")
        require(errors, "ALL" in nested(security, "capabilities", "drop", default=[]), f"{name}: Qdrant must drop all Linux capabilities")
        require(errors, bool(nested(qdrant, "resources", "requests")), f"{name}: Qdrant must set resource requests")
        require(errors, bool(nested(qdrant, "resources", "limits")), f"{name}: Qdrant must set resource limits")
        ports = {port.get("name") for port in qdrant.get("ports", [])}
        require(errors, {"http", "grpc"} <= ports, f"{name}: Qdrant must expose HTTP and gRPC ports")
    require(errors, bool(find_kind(docs, "Service")), f"{name}: Qdrant chart must render a Service")
    require(errors, bool(find_kind(docs, "ServiceAccount")), f"{name}: Qdrant chart must render a ServiceAccount")
    service_accounts = find_kind(docs, "ServiceAccount")
    if service_accounts:
        require(errors, service_accounts[0].get("automountServiceAccountToken") is False, f"{name}: Qdrant ServiceAccount must disable token automount")
    require(errors, bool(find_kind(docs, "NetworkPolicy")), f"{name}: Qdrant chart must render a NetworkPolicy")
    require(errors, bool(find_kind(docs, "PodDisruptionBudget")), f"{name}: Qdrant chart must render a PodDisruptionBudget")
    if expect_pvc:
        require(errors, bool(find_kind(docs, "PersistentVolumeClaim")), f"{name}: customer Qdrant profile must render a PersistentVolumeClaim")


def check_rag_render(name: str, docs: list[dict[str, Any]], expect_hpa: bool, errors: list[str]) -> None:
    deployments = find_kind(docs, "Deployment")
    require(errors, len(deployments) == 1, f"{name}: expected one RAG Deployment")
    if deployments:
        deployment = deployments[0]
        pod_spec = deployment["spec"]["template"]["spec"]
        rag = container(deployment)
        security = rag.get("securityContext", {})
        image = rag.get("image", "")
        required_env = {
            "RAG_DOCUMENT_DIR",
            "RAG_RETRIEVAL_BACKEND",
            "DEFAULT_SANDBOX_ID",
            "AUDIT_LOG_ENABLED",
            "MAX_QUERY_CHARS",
            "DEFAULT_TOP_K",
            "MAX_TOP_K",
            "MAX_CONTEXT_CHARS",
            "QDRANT_URL",
            "QDRANT_COLLECTION",
            "QDRANT_TIMEOUT_SECONDS",
            "QDRANT_VECTOR_DIMENSIONS",
            "QDRANT_BOOTSTRAP_FROM_KNOWLEDGE",
            "API_KEY_AUTH_ENABLED",
            "API_KEY_HEADER",
            "API_KEY_SHA256S",
        }
        missing_env = required_env - env_names(deployment)
        require(errors, ":latest" not in image, f"{name}: RAG image tag must be pinned")
        require(errors, pod_spec.get("automountServiceAccountToken") is False, f"{name}: RAG pod must disable service account token automount")
        require(errors, nested(pod_spec, "securityContext", "runAsNonRoot") is True, f"{name}: RAG pod must run as non-root")
        require(errors, "topologySpreadConstraints" in pod_spec, f"{name}: RAG should render topology spread constraints")
        require(errors, not missing_env, f"{name}: RAG Deployment is missing env vars: {sorted(missing_env)}")
        require(errors, security.get("allowPrivilegeEscalation") is False, f"{name}: RAG must block privilege escalation")
        require(errors, security.get("readOnlyRootFilesystem") is True, f"{name}: RAG must use a read-only root filesystem")
        require(errors, "ALL" in nested(security, "capabilities", "drop", default=[]), f"{name}: RAG must drop all Linux capabilities")
        require(errors, bool(nested(rag, "resources", "requests")), f"{name}: RAG must set resource requests")
        require(errors, bool(nested(rag, "resources", "limits")), f"{name}: RAG must set resource limits")
        mounts = rag.get("volumeMounts", [])
        require(errors, any(mount.get("readOnly") is True for mount in mounts), f"{name}: RAG knowledge mount must be read-only")
    require(errors, bool(find_kind(docs, "ConfigMap")), f"{name}: RAG chart must render a knowledge ConfigMap")
    require(errors, bool(find_kind(docs, "Service")), f"{name}: RAG chart must render a Service")
    require(errors, bool(find_kind(docs, "ServiceAccount")), f"{name}: RAG chart must render a ServiceAccount")
    service_accounts = find_kind(docs, "ServiceAccount")
    if service_accounts:
        require(errors, service_accounts[0].get("automountServiceAccountToken") is False, f"{name}: RAG ServiceAccount must disable token automount")
    require(errors, bool(find_kind(docs, "NetworkPolicy")), f"{name}: RAG chart must render a NetworkPolicy")
    require(errors, bool(find_kind(docs, "PodDisruptionBudget")), f"{name}: RAG chart must render a PodDisruptionBudget")
    if expect_hpa:
        require(errors, bool(find_kind(docs, "HorizontalPodAutoscaler")), f"{name}: customer RAG profile must render an HPA")


def check_agent_workspace_render(name: str, docs: list[dict[str, Any]], errors: list[str]) -> None:
    namespaces = find_kind(docs, "Namespace")
    require(errors, len(namespaces) == 1, f"{name}: agent workspace chart must render a Namespace")
    if namespaces:
        labels = namespaces[0].get("metadata", {}).get("labels", {})
        require(errors, labels.get("platform.ai/traceable-sandbox") == "true", f"{name}: agent Namespace must be traceable")
        require(errors, labels.get("platform.ai/workload-kind") == "coding-agent", f"{name}: agent Namespace must identify coding-agent workload kind")
        require(errors, labels.get("pod-security.kubernetes.io/enforce") == "restricted", f"{name}: agent Namespace must enforce restricted pod security")
    require(errors, bool(find_kind(docs, "ResourceQuota")), f"{name}: agent workspace must render a ResourceQuota")
    require(errors, bool(find_kind(docs, "LimitRange")), f"{name}: agent workspace must render a LimitRange")
    require(errors, bool(find_kind(docs, "PersistentVolumeClaim")), f"{name}: agent workspace must render a workspace PVC")
    require(errors, bool(find_kind(docs, "ConfigMap")), f"{name}: agent workspace must render a platform contract ConfigMap")
    require(errors, bool(find_kind(docs, "ServiceAccount")), f"{name}: agent workspace must render a ServiceAccount")
    service_accounts = find_kind(docs, "ServiceAccount")
    if service_accounts:
        require(errors, service_accounts[0].get("automountServiceAccountToken") is False, f"{name}: agent ServiceAccount must disable token automount")
    require(errors, bool(find_kind(docs, "Role")), f"{name}: agent workspace must render namespace-scoped RBAC")
    require(errors, bool(find_kind(docs, "RoleBinding")), f"{name}: agent workspace must render a RoleBinding")
    policies = find_kind(docs, "NetworkPolicy")
    require(errors, len(policies) >= 2, f"{name}: agent workspace must render default-deny and approved-egress NetworkPolicies")
    default_deny = next((policy for policy in policies if policy.get("metadata", {}).get("name") == "agent-workspace-default-deny"), None)
    require(errors, default_deny is not None, f"{name}: agent workspace missing default-deny NetworkPolicy")
    if default_deny:
        require(errors, "Ingress" in default_deny.get("spec", {}).get("policyTypes", []), f"{name}: agent default-deny must include Ingress")
        require(errors, "Egress" in default_deny.get("spec", {}).get("policyTypes", []), f"{name}: agent default-deny must include Egress")
        require(errors, "ingress" not in default_deny.get("spec", {}), f"{name}: agent default-deny must not define ingress allows")
        require(errors, "egress" not in default_deny.get("spec", {}), f"{name}: agent default-deny must not define egress allows")
    configmaps = find_kind(docs, "ConfigMap")
    if configmaps:
        data = configmaps[0].get("data", {})
        require(errors, "gateway-url" in data, f"{name}: agent platform contract must publish gateway-url")
        require(errors, "rag-url" in data, f"{name}: agent platform contract must publish rag-url")
        require(errors, "compliance-profile" in data, f"{name}: agent platform contract must publish compliance-profile")
        require(errors, "data-classification" in data, f"{name}: agent platform contract must publish data-classification")


def check_vllm_render(name: str, docs: list[dict[str, Any]], resource_name: str, errors: list[str]) -> None:
    deployments = find_kind(docs, "Deployment")
    require(errors, len(deployments) == 1, f"{name}: expected one vLLM Deployment")
    if not deployments:
        return
    deployment = deployments[0]
    pod_spec = deployment["spec"]["template"]["spec"]
    vllm = container(deployment)
    vllm_security = vllm.get("securityContext", {})
    resources = vllm.get("resources", {})
    requests = resources.get("requests", {})
    limits = resources.get("limits", {})

    require(errors, pod_spec.get("automountServiceAccountToken") is False, f"{name}: vLLM pod must disable service account token automount")
    require(errors, nested(pod_spec, "securityContext", "runAsNonRoot") is True, f"{name}: vLLM pod must run as non-root")
    require(errors, "topologySpreadConstraints" in pod_spec, f"{name}: vLLM should render topology spread constraints for multi-replica placement")
    require(errors, vllm_security.get("allowPrivilegeEscalation") is False, f"{name}: vLLM must block privilege escalation")
    require(errors, "ALL" in nested(vllm_security, "capabilities", "drop", default=[]), f"{name}: vLLM must drop all Linux capabilities")
    require(errors, resource_name in requests, f"{name}: vLLM requests must include {resource_name}")
    require(errors, resource_name in limits, f"{name}: vLLM limits must include {resource_name}")
    require(errors, bool(find_kind(docs, "ServiceAccount")), f"{name}: vLLM chart must render a ServiceAccount")
    require(errors, bool(find_kind(docs, "PodDisruptionBudget")), f"{name}: vLLM chart must render a PodDisruptionBudget")
    require(errors, bool(find_kind(docs, "HorizontalPodAutoscaler")), f"{name}: vLLM profile must render an HPA")


def check_model_catalog(errors: list[str]) -> None:
    catalog_path = ROOT / "model-catalog/models.yaml"
    require(errors, catalog_path.exists(), "model catalog must exist at model-catalog/models.yaml")
    if not catalog_path.exists():
        return
    catalog = yaml.safe_load(catalog_path.read_text())
    models = nested(catalog, "spec", "models", default=[])
    model_ids = {model.get("id") for model in models}
    require(errors, "qwen2.5:0.5b" in model_ids, "model catalog must include the local Ollama smoke model")
    require(errors, "TinyLlama/TinyLlama-1.1B-Chat-v1.0" in model_ids, "model catalog must include the vLLM customer lab model")
    for model in models:
        model_id = model.get("id", "<unknown>")
        require(errors, bool(model.get("owner")), f"model catalog entry {model_id} must define owner")
        require(errors, model.get("status") in {"proposed", "approved", "deprecated", "blocked"}, f"model catalog entry {model_id} must define a valid lifecycle status")
        require(errors, isinstance(model.get("contextWindow"), int) and model.get("contextWindow") > 0, f"model catalog entry {model_id} must define a positive contextWindow")
    for environment in ("local", "customer"):
        values = yaml.safe_load((ROOT / f"clusters/{environment}/values/inference-gateway.yaml").read_text())
        for model_id in nested(values, "runtime", "allowedModels", default=[]):
            require(errors, model_id in model_ids, f"{environment}: allowed model {model_id} is missing from model catalog")
    configmap = ROOT / "model-catalog/k8s/configmap.yaml"
    require(errors, configmap.exists(), "model catalog ConfigMap must exist")
    if configmap.exists():
        docs = load_yaml_documents(configmap)
        require(errors, bool(find_kind(docs, "ConfigMap")), "model catalog ConfigMap must render as a ConfigMap")


def check_model_governance(errors: list[str]) -> None:
    script = ROOT / "scripts/model-catalog.py"
    require(errors, os.access(script, os.X_OK), "scripts/model-catalog.py must be executable")
    require(errors, (ROOT / "runbooks/model-governance.md").exists(), "model governance runbook must exist")
    require(errors, (ROOT / "results/model-catalog/sample-summary.md").exists(), "model catalog sample summary must exist")
    require(errors, (ROOT / "model-catalog/promotion-requests/qwen-local-lab-approved.yaml").exists(), "qwen model promotion request must exist")
    require(errors, (ROOT / "model-catalog/promotion-requests/tinyllama-customer-lab-approved.yaml").exists(), "TinyLlama model promotion request must exist")
    if script.exists():
        completed = subprocess.run(
            [sys.executable, str(script), "--check"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        require(errors, completed.returncode == 0, f"model catalog governance check failed: {completed.stderr or completed.stdout}")


def check_sandbox(errors: list[str]) -> None:
    sandbox_docs: list[dict[str, Any]] = []
    for path in sorted((ROOT / "sandbox/base").glob("*.yaml")):
        sandbox_docs.extend(load_yaml_documents(path))

    namespaces = find_kind(sandbox_docs, "Namespace")
    require(errors, len(namespaces) == 1, "sandbox: expected one Namespace")
    if namespaces:
        labels = namespaces[0].get("metadata", {}).get("labels", {})
        require(errors, labels.get("platform.ai/traceable-sandbox") == "true", "sandbox: namespace must be labeled as traceable")
        require(errors, labels.get("pod-security.kubernetes.io/enforce") == "restricted", "sandbox: namespace must enforce restricted pod security")
        require(errors, labels.get("platform.ai/sandbox-id") == "local-lab", "sandbox: namespace must carry sandbox id label")

    quotas = find_kind(sandbox_docs, "ResourceQuota")
    require(errors, len(quotas) == 1, "sandbox: expected one ResourceQuota")
    if quotas:
        hard = quotas[0].get("spec", {}).get("hard", {})
        for key in ("requests.cpu", "requests.memory", "limits.cpu", "limits.memory", "pods"):
            require(errors, key in hard, f"sandbox: ResourceQuota missing {key}")

    require(errors, bool(find_kind(sandbox_docs, "LimitRange")), "sandbox: expected a LimitRange")
    policies = find_kind(sandbox_docs, "NetworkPolicy")
    require(errors, len(policies) >= 2, "sandbox: expected default-deny and allowlist NetworkPolicies")
    default_deny = next((policy for policy in policies if policy.get("metadata", {}).get("name") == "ai-sandbox-default-deny"), None)
    require(errors, default_deny is not None, "sandbox: missing default-deny NetworkPolicy")
    if default_deny:
        require(errors, "Ingress" in default_deny.get("spec", {}).get("policyTypes", []), "sandbox: default-deny must include Ingress")
        require(errors, "Egress" in default_deny.get("spec", {}).get("policyTypes", []), "sandbox: default-deny must include Egress")
        require(errors, "ingress" not in default_deny.get("spec", {}), "sandbox: default-deny must not define ingress allows")
        require(errors, "egress" not in default_deny.get("spec", {}), "sandbox: default-deny must not define egress allows")


def check_evals(errors: list[str]) -> None:
    suite_path = ROOT / "evals/smoke-suite.yaml"
    require(errors, suite_path.exists(), "eval smoke suite must exist at evals/smoke-suite.yaml")
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
    coding_agent_suite = ROOT / "evals/coding-agent-suite.yaml"
    require(errors, coding_agent_suite.exists(), "coding-agent eval suite must exist at evals/coding-agent-suite.yaml")
    require(errors, (ROOT / "results/evals/sample-coding-agent-summary.md").exists(), "coding-agent eval sample summary must exist")
    require(errors, (ROOT / "results/evals/sample-coding-agent-summary.json").exists(), "coding-agent eval sample JSON must exist")
    if coding_agent_suite.exists():
        suite = yaml.safe_load(coding_agent_suite.read_text())
        cases = nested(suite, "spec", "cases", default=[])
        require(errors, suite.get("kind") == "EvalSuite", "coding-agent eval suite must use kind EvalSuite")
        require(errors, isinstance(cases, list) and len(cases) >= 4, "coding-agent eval suite must define at least four cases")
        require(errors, "forbiddenAny" in coding_agent_suite.read_text(), "coding-agent eval suite must include forbiddenAny secret-leak checks")
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts/eval-suite.py"), "--suite", "evals/coding-agent-suite.yaml", "--check-config"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        require(errors, completed.returncode == 0, f"coding-agent eval suite check failed: {completed.stderr or completed.stdout}")


def check_tenant_labs(errors: list[str]) -> None:
    tenant_example = ROOT / "tenants/examples/team-a-lab.yaml"
    require(errors, tenant_example.exists(), "tenant example must exist at tenants/examples/team-a-lab.yaml")
    if tenant_example.exists():
        docs = load_yaml_documents(tenant_example)
        require(errors, bool(find_kind(docs, "Namespace")), "tenant example must define a Namespace")
        require(errors, bool(find_kind(docs, "ResourceQuota")), "tenant example must define a ResourceQuota")
        require(errors, bool(find_kind(docs, "LimitRange")), "tenant example must define a LimitRange")
        require(errors, len(find_kind(docs, "NetworkPolicy")) >= 2, "tenant example must define default-deny and allowlist NetworkPolicies")
        require(errors, bool(find_kind(docs, "ConfigMap")), "tenant example must define a trace contract ConfigMap")
        require(errors, bool(find_kind(docs, "Role")), "tenant example must define a Role")
        require(errors, bool(find_kind(docs, "RoleBinding")), "tenant example must define a RoleBinding")
        configmaps = find_kind(docs, "ConfigMap")
        if configmaps:
            require(errors, "rag-url" in configmaps[0].get("data", {}), "tenant example trace contract must publish rag-url")
        namespace = next(iter(find_kind(docs, "Namespace")), {})
        labels = namespace.get("metadata", {}).get("labels", {})
        for key in ("platform.ai/tenant", "platform.ai/sandbox-id", "platform.ai/traceable-sandbox", "pod-security.kubernetes.io/enforce"):
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
    if script.exists() and spec.exists():
        completed = subprocess.run(
            [sys.executable, str(script), "--spec", str(spec.relative_to(ROOT)), "--check"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        require(errors, completed.returncode == 0, f"tenant onboarding check failed: {completed.stderr or completed.stdout}")
        onboarding = yaml.safe_load(spec.read_text())
        require(errors, nested(onboarding, "spec", "tenant", "namespace") == "ai-coding-agents", "tenant onboarding spec should define the coding-agent namespace")
        require(errors, nested(onboarding, "spec", "agentWorkspace", "enabled") is True, "tenant onboarding spec should enable agent workspace output")
        require(errors, bool(nested(onboarding, "spec", "network", "allowedEgressCidrs", default=[])), "tenant onboarding spec should include an approved external egress example")
    if script.exists() and regulated_spec.exists():
        completed = subprocess.run(
            [sys.executable, str(script), "--spec", str(regulated_spec.relative_to(ROOT)), "--check"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        require(errors, completed.returncode == 0, f"regulated/offline tenant onboarding check failed: {completed.stderr or completed.stdout}")
        onboarding = yaml.safe_load(regulated_spec.read_text())
        require(errors, nested(onboarding, "spec", "tenant", "namespace") == "ai-regulated-agents", "regulated/offline onboarding spec should define the regulated namespace")
        require(errors, nested(onboarding, "spec", "compliance", "profile") == "regulated-offline", "regulated/offline onboarding spec must set compliance profile")
        require(errors, nested(onboarding, "spec", "compliance", "externalEgressAllowed") is False, "regulated/offline onboarding spec must disallow external egress")
        require(errors, nested(onboarding, "spec", "network", "allowedEgressCidrs", default=[]) == [], "regulated/offline onboarding spec must not include external CIDR egress")
        require(errors, nested(onboarding, "spec", "agentWorkspace", "rbac", "allowJobManagement") is False, "regulated/offline onboarding spec should disable job management by default")


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
            require(errors, action in allowed_actions, f"{path.relative_to(ROOT)} action must be one of {sorted(allowed_actions)}")
            require(errors, bool(nested(doc, "spec", "target", "name")), f"{path.relative_to(ROOT)} must define target name")
            require(errors, bool(nested(doc, "spec", "target", "kind")), f"{path.relative_to(ROOT)} must define target kind")
            if action == "rollout-restart":
                require(errors, bool(nested(doc, "spec", "target", "namespace")), f"{path.relative_to(ROOT)} rollout drill must define target namespace")
            require(errors, bool(nested(doc, "spec", "validation", "command")), f"{path.relative_to(ROOT)} must define validation command")
            require(errors, nested(doc, "spec", "safety", "destructive") is False, f"{path.relative_to(ROOT)} must be marked non-destructive")
            require(errors, isinstance(nested(doc, "spec", "safety", "maxDurationSeconds"), int), f"{path.relative_to(ROOT)} must define safety.maxDurationSeconds")
    require(errors, expected <= seen, f"chaos drills missing expected definitions: {sorted(expected - seen)}")
    require(errors, os.access(ROOT / "scripts/chaos-drill.sh", os.X_OK), "scripts/chaos-drill.sh must be executable")
    if (ROOT / "scripts/chaos-drill.sh").exists():
        source = (ROOT / "scripts/chaos-drill.sh").read_text()
        for phrase in ("gpu-capacity-preflight", "qdrant-vector-store-rollout", "vllm-runtime-rollout", "rag-service-rollout"):
            require(errors, phrase in source, f"chaos runner missing {phrase}")


def check_evidence_pack(errors: list[str]) -> None:
    require(errors, os.access(ROOT / "scripts/evidence-pack.py", os.X_OK), "scripts/evidence-pack.py must be executable")
    require(errors, (ROOT / "results/evidence/sample-summary.md").exists(), "evidence pack sample summary must exist")
    require(errors, (ROOT / "runbooks/evidence-pack.md").exists(), "evidence pack runbook must exist")
    script = ROOT / "scripts/evidence-pack.py"
    if script.exists():
        source = script.read_text()
        for phrase in ("OpenAI-compatible gateway", "RAG service", "Vector RAG profile", "Coding-agent workspaces", "Tenant onboarding workflow", "Regulated offline tenant profile", "Advanced chaos drills", "Restore-drill integration", "NVIDIA and AMD accelerator profiles"):
            require(errors, phrase in source, f"evidence pack script missing control phrase {phrase}")


def check_validation_toolchain(errors: list[str]) -> None:
    manifest_path = ROOT / "tools/validation-toolchain.yaml"
    script = ROOT / "scripts/toolchain-doctor.py"
    installer = ROOT / "scripts/install-validation-tools.sh"
    require(errors, manifest_path.exists(), "validation toolchain manifest must exist")
    require(errors, os.access(script, os.X_OK), "scripts/toolchain-doctor.py must be executable")
    require(errors, os.access(installer, os.X_OK), "scripts/install-validation-tools.sh must be executable")
    require(errors, (ROOT / "runbooks/validation-toolchain.md").exists(), "validation toolchain runbook must exist")
    require(errors, (ROOT / "results/toolchain/sample-summary.md").exists(), "validation toolchain sample summary must exist")
    if manifest_path.exists():
        manifest = yaml.safe_load(manifest_path.read_text()) or {}
        require(errors, manifest.get("kind") == "ValidationToolchain", "validation toolchain manifest kind must be ValidationToolchain")
        strict_required = set(nested(manifest, "spec", "profiles", "strict", "required", default=[]))
        expected = {"python3", "helm", "kubeconform", "kyverno", "restore-drill", "k6", "syft", "argocd", "cosign", "trivy"}
        require(errors, expected <= strict_required, f"validation toolchain strict profile missing {sorted(expected - strict_required)}")
        tools = nested(manifest, "spec", "tools", default={})
        for tool in expected - {"python3", "helm"}:
            require(errors, bool(nested(tools, tool, "defaultVersion")), f"validation toolchain {tool} must define defaultVersion")
            require(errors, bool(nested(tools, tool, "installerEnv")), f"validation toolchain {tool} must define installerEnv")
    if installer.exists():
        installer_source = installer.read_text()
        for phrase in ("sha256sum", "KUBECONFORM_VERSION", "KYVERNO_VERSION", "TRIVY_VERSION", "COSIGN_VERSION"):
            require(errors, phrase in installer_source, f"validation tool installer missing {phrase}")
    if script.exists():
        completed = subprocess.run(
            [sys.executable, str(script), "--profile", "validate", "--check"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        require(errors, completed.returncode == 0, f"validation toolchain check failed: {completed.stderr or completed.stdout}")


def check_release_gates(errors: list[str]) -> None:
    config_path = ROOT / "slo/release-gates.yaml"
    script = ROOT / "scripts/release-gate.py"
    require(errors, config_path.exists(), "release gate config must exist at slo/release-gates.yaml")
    require(errors, os.access(script, os.X_OK), "scripts/release-gate.py must be executable")
    require(errors, (ROOT / "runbooks/release-gates.md").exists(), "release gates runbook must exist")
    require(errors, (ROOT / "results/release-gate/sample-summary.md").exists(), "release gate sample summary must exist")
    for path in [
        ROOT / "results/evals/sample-summary.json",
        ROOT / "results/loadtest/sample-summary.json",
        ROOT / "results/evidence/sample-summary.json",
        ROOT / "results/toolchain/sample-summary.json",
    ]:
        require(errors, path.exists(), f"release gate sample evidence missing {path.relative_to(ROOT)}")
    if config_path.exists():
        config = yaml.safe_load(config_path.read_text()) or {}
        require(errors, config.get("kind") == "ReleaseGate", "release gate config kind must be ReleaseGate")
        gates = set(nested(config, "spec", "gates", default={}))
        expected = {"eval", "load", "restore", "toolchain", "egress", "retention", "slo", "quota", "modelProvenance", "evidencePack"}
        require(errors, expected <= gates, f"release gate config missing {sorted(expected - gates)}")
    if script.exists():
        completed = subprocess.run(
            [sys.executable, str(script), "--check"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        require(errors, completed.returncode == 0, f"release gate check failed: {completed.stderr or completed.stdout}")


def check_slo_governance(errors: list[str]) -> None:
    config_path = ROOT / "slo/objectives.yaml"
    script = ROOT / "scripts/slo-report.py"
    require(errors, config_path.exists(), "SLO objective config must exist at slo/objectives.yaml")
    require(errors, os.access(script, os.X_OK), "scripts/slo-report.py must be executable")
    require(errors, (ROOT / "runbooks/slo-error-budget.md").exists(), "SLO error budget runbook must exist")
    require(errors, (ROOT / "results/slo/sample-summary.md").exists(), "SLO sample summary must exist")
    require(errors, (ROOT / "results/slo/sample-summary.json").exists(), "SLO sample JSON must exist")
    if config_path.exists():
        config = yaml.safe_load(config_path.read_text()) or {}
        require(errors, config.get("kind") == "SLOSet", "SLO objective config kind must be SLOSet")
        objectives = nested(config, "spec", "objectives", default=[])
        objective_ids = {item.get("id") for item in objectives if isinstance(item, dict)}
        expected = {"inference-availability", "inference-latency", "eval-quality-smoke", "restore-verification", "agent-platform-readiness"}
        require(errors, expected <= objective_ids, f"SLO objective config missing {sorted(expected - objective_ids)}")
    alerts = (ROOT / "observability/alerts/ai-platform-alerts.yaml").read_text()
    for alert in ("InferenceGatewayErrorBudgetFastBurn", "InferenceGatewayErrorBudgetSlowBurn", "InferenceGatewayHighLatency", "RestoreDrillFailed"):
        require(errors, alert in alerts, f"SLO alert coverage missing {alert}")
    if script.exists():
        completed = subprocess.run(
            [sys.executable, str(script), "--check"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        require(errors, completed.returncode == 0, f"SLO check failed: {completed.stderr or completed.stdout}")


def check_quota_governance(errors: list[str]) -> None:
    policy_path = ROOT / "governance/quota-plans.yaml"
    script = ROOT / "scripts/quota-check.py"
    require(errors, policy_path.exists(), "quota plan policy must exist at governance/quota-plans.yaml")
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
        required_labels = {"platform.ai/owner", "platform.ai/cost-center", "platform.ai/environment", "platform.ai/sandbox-id"}
        require(errors, required_labels <= labels, f"quota plan policy missing chargeback labels {sorted(required_labels - labels)}")
    if script.exists():
        completed = subprocess.run(
            [sys.executable, str(script), "--check"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        require(errors, completed.returncode == 0, f"quota check failed: {completed.stderr or completed.stdout}")


def check_model_provenance_governance(errors: list[str]) -> None:
    policy_path = ROOT / "governance/model-provenance.yaml"
    script = ROOT / "scripts/model-provenance.py"
    require(errors, policy_path.exists(), "model provenance policy must exist at governance/model-provenance.yaml")
    require(errors, os.access(script, os.X_OK), "scripts/model-provenance.py must be executable")
    require(errors, (ROOT / "runbooks/model-provenance.md").exists(), "model provenance runbook must exist")
    require(errors, (ROOT / "results/model-provenance/sample-summary.md").exists(), "model provenance sample summary must exist")
    require(errors, (ROOT / "results/model-provenance/sample-summary.json").exists(), "model provenance sample JSON must exist")
    if policy_path.exists():
        policy = yaml.safe_load(policy_path.read_text()) or {}
        require(errors, policy.get("kind") == "ModelProvenanceSet", "model provenance policy kind must be ModelProvenanceSet")
        artifacts = nested(policy, "spec", "artifacts", default=[])
        model_ids = {item.get("modelId") for item in artifacts if isinstance(item, dict)}
        expected = {"qwen2.5:0.5b", "TinyLlama/TinyLlama-1.1B-Chat-v1.0"}
        require(errors, expected <= model_ids, f"model provenance policy missing {sorted(expected - model_ids)}")
        required = set(nested(policy, "spec", "requiredEvidence", default=[]))
        required_fields = {"sourceUri", "immutableRef", "digest", "license", "dataClassification", "riskTier", "promotionRequest", "servingProfiles"}
        require(errors, required_fields <= required, f"model provenance policy missing required evidence {sorted(required_fields - required)}")
    if script.exists():
        completed = subprocess.run(
            [sys.executable, str(script), "--check"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        require(errors, completed.returncode == 0, f"model provenance check failed: {completed.stderr or completed.stdout}")


def check_egress_governance(errors: list[str]) -> None:
    catalog_path = ROOT / "network/egress-catalog.yaml"
    script = ROOT / "scripts/egress-governance.py"
    require(errors, catalog_path.exists(), "egress governance catalog must exist at network/egress-catalog.yaml")
    require(errors, os.access(script, os.X_OK), "scripts/egress-governance.py must be executable")
    require(errors, (ROOT / "runbooks/egress-governance.md").exists(), "egress governance runbook must exist")
    require(errors, (ROOT / "results/egress-governance/sample-summary.md").exists(), "egress governance sample summary must exist")
    require(errors, (ROOT / "results/egress-governance/sample-summary.json").exists(), "egress governance sample JSON must exist")
    if catalog_path.exists():
        catalog = yaml.safe_load(catalog_path.read_text()) or {}
        require(errors, catalog.get("kind") == "ApprovedEgressCatalog", "egress governance catalog kind must be ApprovedEgressCatalog")
        entries = nested(catalog, "spec", "entries", default=[])
        require(errors, isinstance(entries, list) and bool(entries), "egress governance catalog must define entries")
        require(errors, "customer-git-artifact-mirror-example" in {entry.get("id") for entry in entries if isinstance(entry, dict)}, "egress governance catalog must include customer mirror example")
    onboarding = yaml.safe_load((ROOT / "tenants/onboarding/coding-agents.yaml").read_text())
    for item in nested(onboarding, "spec", "network", "allowedEgressCidrs", default=[]):
        require(errors, bool(item.get("catalogRef")), "tenant onboarding external egress must include catalogRef")
    if script.exists():
        completed = subprocess.run(
            [sys.executable, str(script), "--check"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        require(errors, completed.returncode == 0, f"egress governance check failed: {completed.stderr or completed.stdout}")


def check_retention_governance(errors: list[str]) -> None:
    policy_path = ROOT / "governance/data-retention.yaml"
    script = ROOT / "scripts/retention-check.py"
    require(errors, policy_path.exists(), "data retention policy must exist at governance/data-retention.yaml")
    require(errors, os.access(script, os.X_OK), "scripts/retention-check.py must be executable")
    require(errors, (ROOT / "runbooks/data-retention.md").exists(), "data retention runbook must exist")
    require(errors, (ROOT / "results/retention/sample-summary.md").exists(), "data retention sample summary must exist")
    require(errors, (ROOT / "results/retention/sample-summary.json").exists(), "data retention sample JSON must exist")
    if policy_path.exists():
        policy = yaml.safe_load(policy_path.read_text()) or {}
        require(errors, policy.get("kind") == "DataRetentionPolicy", "data retention policy kind must be DataRetentionPolicy")
        classes = set(nested(policy, "spec", "classes", default={}))
        expected = {"auditLogs", "generatedEvidence", "ragKnowledge", "agentWorkspace", "modelGovernance"}
        require(errors, expected <= classes, f"data retention policy missing {sorted(expected - classes)}")
        audit = nested(policy, "spec", "classes", "auditLogs", default={})
        require(errors, audit.get("storesRawPrompt") is False, "data retention policy must disallow raw prompts")
        require(errors, audit.get("storesRawQuery") is False, "data retention policy must disallow raw RAG queries")
    if script.exists():
        completed = subprocess.run(
            [sys.executable, str(script), "--check"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        require(errors, completed.returncode == 0, f"data retention check failed: {completed.stderr or completed.stdout}")


def check_values_and_docs(errors: list[str]) -> None:
    for environment in ("local", "customer"):
        values = yaml.safe_load((ROOT / f"clusters/{environment}/values/inference-gateway.yaml").read_text())
        allowed = nested(values, "runtime", "allowedModels", default=[])
        require(errors, bool(allowed), f"{environment}: inference gateway values must define runtime.allowedModels")
        admission = values.get("admission", {})
        for key in ("maxMessages", "maxPromptChars", "maxCompletionTokens", "allowStreaming"):
            require(errors, key in admission, f"{environment}: inference gateway values must define admission.{key}")
        guardrails = values.get("guardrails", {})
        prompt_secret_detection = nested(guardrails, "promptSecretDetection", default={})
        require(errors, prompt_secret_detection.get("enabled") is True, f"{environment}: prompt secret detection should be enabled")
        patterns = prompt_secret_detection.get("patterns", [])
        require(errors, isinstance(patterns, list) and bool(patterns), f"{environment}: prompt secret detection patterns must be set")
        require(errors, "private_key" in patterns, f"{environment}: prompt secret detection should include private_key")
        require(errors, "generic_api_key_assignment" in patterns, f"{environment}: prompt secret detection should include generic_api_key_assignment")
        budget = values.get("budget", {})
        for key in ("enabled", "backend", "requestLimit", "promptCharLimit", "estimatedTokenLimit", "estimatedCharsPerToken", "windowSeconds", "redisUrl", "redisTimeoutSeconds", "keyPrefix"):
            require(errors, key in budget, f"{environment}: inference gateway values must define budget.{key}")
        require(errors, budget.get("enabled") is True, f"{environment}: sandbox budgets should be enabled")
        require(errors, budget.get("backend") == "redis", f"{environment}: sandbox budget backend should be redis for shared multi-replica accounting")
        require(errors, str(budget.get("redisUrl", "")).startswith("redis://"), f"{environment}: budget.redisUrl must be a Redis URL")
        for key in ("requestLimit", "promptCharLimit", "estimatedTokenLimit", "estimatedCharsPerToken", "windowSeconds"):
            require(errors, isinstance(budget.get(key), int) and budget.get(key) > 0, f"{environment}: budget.{key} must be a positive integer")
        auth = values.get("auth", {})
        require(errors, auth.get("enabled") is True, f"{environment}: inference gateway auth.enabled should be true")
        require(errors, bool(auth.get("apiKeyHeader")), f"{environment}: inference gateway auth.apiKeyHeader must be set")
        if environment == "local":
            require(errors, bool(auth.get("apiKeyHashes")), f"{environment}: inference gateway auth.apiKeyHashes must be set for local smoke tests")
        else:
            require(errors, bool(nested(auth, "existingSecret", "name")), f"{environment}: inference gateway auth existingSecret.name must be set")
            require(errors, bool(nested(auth, "existingSecret", "key")), f"{environment}: inference gateway auth existingSecret.key must be set")
        rag_values_path = ROOT / f"clusters/{environment}/values/rag-service.yaml"
        qdrant_values_path = ROOT / f"clusters/{environment}/values/qdrant-vector-store.yaml"
        agent_values_path = ROOT / f"clusters/{environment}/values/agent-workspace.yaml"
        require(errors, rag_values_path.exists(), f"{environment}: RAG service values must exist")
        require(errors, qdrant_values_path.exists(), f"{environment}: Qdrant vector-store values must exist")
        require(errors, agent_values_path.exists(), f"{environment}: agent workspace values must exist")
        if rag_values_path.exists():
            rag_values = yaml.safe_load(rag_values_path.read_text())
            require(errors, nested(rag_values, "traceability", "defaultSandboxId") is not None, f"{environment}: RAG values must define traceability.defaultSandboxId")
            expected_backend = "lexical" if environment == "local" else "qdrant"
            require(errors, nested(rag_values, "retrieval", "backend") == expected_backend, f"{environment}: RAG retrieval.backend should be {expected_backend}")
            require(errors, nested(rag_values, "retrieval", "vectorStore", "collection") is not None, f"{environment}: RAG vectorStore.collection must be set")
            require(errors, nested(rag_values, "retrieval", "vectorStore", "dimensions", default=0) > 0, f"{environment}: RAG vectorStore.dimensions must be positive")
            if environment == "customer":
                require(errors, str(nested(rag_values, "retrieval", "vectorStore", "url", default="")).startswith("http://qdrant-vector-store.vector.svc"), f"{environment}: RAG vectorStore.url should point at the vector namespace service")
            rag_auth = rag_values.get("auth", {})
            require(errors, rag_auth.get("enabled") is True, f"{environment}: RAG auth.enabled should be true")
            require(errors, bool(rag_auth.get("apiKeyHeader")), f"{environment}: RAG auth.apiKeyHeader must be set")
            if environment == "local":
                require(errors, bool(rag_auth.get("apiKeyHashes")), f"{environment}: RAG auth.apiKeyHashes must be set for local smoke tests")
            else:
                require(errors, bool(nested(rag_auth, "existingSecret", "name")), f"{environment}: RAG auth existingSecret.name must be set")
                require(errors, bool(nested(rag_auth, "existingSecret", "key")), f"{environment}: RAG auth existingSecret.key must be set")
        if qdrant_values_path.exists():
            qdrant_values = yaml.safe_load(qdrant_values_path.read_text())
            require(errors, nested(qdrant_values, "resources", "requests", "memory") is not None, f"{environment}: Qdrant values must set memory requests")
            require(errors, nested(qdrant_values, "resources", "limits", "memory") is not None, f"{environment}: Qdrant values must set memory limits")
            if environment == "customer":
                require(errors, nested(qdrant_values, "persistence", "enabled") is True, f"{environment}: Qdrant persistence should be enabled")
                require(errors, bool(nested(qdrant_values, "persistence", "size")), f"{environment}: Qdrant persistence size must be set")
        if agent_values_path.exists():
            agent_values = yaml.safe_load(agent_values_path.read_text())
            require(errors, nested(agent_values, "sandbox", "id") is not None, f"{environment}: agent workspace values must define sandbox.id")

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
    production_doc = (ROOT / "docs/production-readiness.md").read_text()
    for phrase in ("API authentication", "Traceability", "Model governance", "Model lifecycle", "Model provenance", "Prompt secret detection", "Sandbox budgets", "Shared budget backend", "Quota and chargeback", "RAG service", "Vector RAG profile", "Agent workspaces", "Egress governance", "Data retention", "SLO and error budget", "Tenant labs", "Chaos drills", "Evaluation harness", "Evidence pack", "Validation toolchain", "make toolchain-install", "Release gates", "Sandbox isolation", "Backup and restore", "Supply chain"):
        require(errors, phrase in production_doc, f"production readiness matrix missing {phrase}")
    policies = (ROOT / "policies/kyverno/policies.yaml").read_text()
    require(errors, "platform.ai/sandbox-id" in policies, "Kyverno policies must require sandbox id labels")
    require(errors, os.access(ROOT / "scripts/sandbox-smoke.sh", os.X_OK), "scripts/sandbox-smoke.sh must be executable")
    require(errors, os.access(ROOT / "scripts/rag-smoke.sh", os.X_OK), "scripts/rag-smoke.sh must be executable")
    require(errors, os.access(ROOT / "scripts/agent-lab-up.sh", os.X_OK), "scripts/agent-lab-up.sh must be executable")
    require(errors, os.access(ROOT / "scripts/agent-smoke.sh", os.X_OK), "scripts/agent-smoke.sh must be executable")


def main() -> int:
    errors: list[str] = []
    try:
        check_agent_workspace_render("agent-workspace-defaults", render_chart("agent-workspace"), errors)
        for environment in ("local", "customer"):
            check_agent_workspace_render(
                f"{environment}-agent-workspace",
                render_chart("agent-workspace", ROOT / f"clusters/{environment}/values/agent-workspace.yaml"),
                errors,
            )
        check_budget_redis_render(render_chart("budget-redis"), errors)
        check_qdrant_render("qdrant-defaults", render_chart("qdrant-vector-store"), True, errors)
        check_qdrant_render(
            "local-qdrant-vector-store",
            render_chart("qdrant-vector-store", ROOT / "clusters/local/values/qdrant-vector-store.yaml"),
            False,
            errors,
        )
        check_qdrant_render(
            "customer-qdrant-vector-store",
            render_chart("qdrant-vector-store", ROOT / "clusters/customer/values/qdrant-vector-store.yaml"),
            True,
            errors,
        )
        check_gateway_render("chart-defaults", render_chart("inference-gateway"), errors)
        for environment in ("local", "customer"):
            check_gateway_render(
                environment,
                render_chart("inference-gateway", ROOT / f"clusters/{environment}/values/inference-gateway.yaml"),
                errors,
            )
        check_rag_render("chart-defaults", render_chart("rag-service"), False, errors)
        check_rag_render(
            "local-rag-service",
            render_chart("rag-service", ROOT / "clusters/local/values/rag-service.yaml"),
            False,
            errors,
        )
        check_rag_render(
            "customer-rag-service",
            render_chart("rag-service", ROOT / "clusters/customer/values/rag-service.yaml"),
            True,
            errors,
        )
        check_vllm_render(
            "customer-vllm-nvidia",
            render_chart("vllm", ROOT / "clusters/customer/values/vllm-nvidia.yaml"),
            "nvidia.com/gpu",
            errors,
        )
        check_vllm_render(
            "customer-vllm-amd",
            render_chart("vllm", ROOT / "clusters/customer/values/vllm-amd.yaml"),
            "amd.com/gpu",
            errors,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        errors.append(f"failed to render production charts: {exc}")

    check_sandbox(errors)
    check_model_catalog(errors)
    check_model_governance(errors)
    check_evals(errors)
    check_tenant_labs(errors)
    check_tenant_onboarding(errors)
    check_chaos_drills(errors)
    check_evidence_pack(errors)
    check_validation_toolchain(errors)
    check_release_gates(errors)
    check_slo_governance(errors)
    check_quota_governance(errors)
    check_model_provenance_governance(errors)
    check_egress_governance(errors)
    check_retention_governance(errors)
    check_values_and_docs(errors)

    if errors:
        print("production readiness check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("production readiness controls ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
