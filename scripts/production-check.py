from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG_VERSION_PATTERN = re.compile(r"^## v(?P<version>\d+\.\d+\.\d+) - \d{4}-\d{2}-\d{2}$")
PIN_PATTERN = re.compile(r"^\s*([A-Za-z0-9_.-]+)==([^\s\\]+)")


def load_yaml_documents(path: Path) -> list[dict[str, Any]]:
    docs = []
    for item in yaml.safe_load_all(path.read_text()):
        if isinstance(item, dict):
            docs.append(item)
    return docs


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def latest_changelog_version(errors: list[str]) -> str:
    changelog = ROOT / "CHANGELOG.md"
    require(errors, changelog.exists(), "CHANGELOG.md must exist")
    if not changelog.exists():
        return ""
    for line in changelog.read_text().splitlines():
        match = CHANGELOG_VERSION_PATTERN.fullmatch(line.strip())
        if match:
            return match.group("version")
    errors.append("CHANGELOG.md must start with a version heading like '## v0.0.0 - YYYY-MM-DD'")
    return ""


def render_chart(chart: str, values: Path | None = None) -> list[dict[str, Any]]:
    cmd = ["helm", "template", "production-check", str(ROOT / f"deploy/charts/{chart}")]
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


def normalized_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-r ")):
            continue
        match = PIN_PATTERN.match(stripped)
        if match:
            pins[normalized_package_name(match.group(1))] = match.group(2)
    return pins


def require_lock_contains_pins(errors: list[str], requirements: Path, lockfile: Path, expected: dict[str, str]) -> None:
    if not lockfile.exists():
        return
    lock_text = lockfile.read_text().lower()
    for name, version in expected.items():
        require(errors, f"{name}=={version}" in lock_text, f"{lockfile.relative_to(ROOT)} must include pinned dependency {name}=={version} from {requirements.relative_to(ROOT)}")


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
        "OTEL_TRACING_ENABLED",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_SERVICE_NAME",
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


def check_ollama_render(name: str, docs: list[dict[str, Any]], errors: list[str]) -> None:
    statefulsets = find_kind(docs, "StatefulSet")
    require(errors, len(statefulsets) == 1, f"{name}: expected one Ollama StatefulSet")
    if not statefulsets:
        return
    statefulset = statefulsets[0]
    pod_spec = statefulset["spec"]["template"]["spec"]
    ollama = container(statefulset)
    security = ollama.get("securityContext", {})
    mounts = {mount.get("name"): mount for mount in ollama.get("volumeMounts", [])}
    volumes = {volume.get("name") for volume in pod_spec.get("volumes", [])}

    require(errors, pod_spec.get("automountServiceAccountToken") is False, f"{name}: Ollama pod must disable service account token automount")
    require(errors, nested(pod_spec, "securityContext", "runAsNonRoot") is True, f"{name}: Ollama pod must run as non-root")
    require(errors, "topologySpreadConstraints" in pod_spec, f"{name}: Ollama should render topology spread constraints")
    require(errors, security.get("allowPrivilegeEscalation") is False, f"{name}: Ollama must block privilege escalation")
    require(errors, security.get("readOnlyRootFilesystem") is True, f"{name}: Ollama must use a read-only root filesystem")
    require(errors, "ALL" in nested(security, "capabilities", "drop", default=[]), f"{name}: Ollama must drop all Linux capabilities")
    require(errors, "data" in mounts and mounts["data"].get("mountPath") == "/models", f"{name}: Ollama must mount writable model storage at /models")
    require(errors, "tmp" in mounts and mounts["tmp"].get("mountPath") == "/tmp", f"{name}: Ollama must mount writable tmp storage")
    require(errors, "tmp" in volumes, f"{name}: Ollama must define a tmp emptyDir volume")
    for init in pod_spec.get("initContainers", []):
        init_security = init.get("securityContext", {})
        init_mounts = {mount.get("name"): mount for mount in init.get("volumeMounts", [])}
        require(errors, init_security.get("allowPrivilegeEscalation") is False, f"{name}: Ollama init container must block privilege escalation")
        require(errors, init_security.get("readOnlyRootFilesystem") is True, f"{name}: Ollama init container must use a read-only root filesystem")
        require(errors, "ALL" in nested(init_security, "capabilities", "drop", default=[]), f"{name}: Ollama init container must drop all Linux capabilities")
        require(errors, "data" in init_mounts and "tmp" in init_mounts, f"{name}: Ollama init container must mount model and tmp storage")
    require(errors, bool(find_kind(docs, "Service")), f"{name}: Ollama chart must render a Service")
    require(errors, bool(find_kind(docs, "ServiceAccount")), f"{name}: Ollama chart must render a ServiceAccount")
    require(errors, bool(find_kind(docs, "PodDisruptionBudget")), f"{name}: Ollama chart must render a PodDisruptionBudget")


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
        require(errors, security.get("readOnlyRootFilesystem") is True, f"{name}: Qdrant must use a read-only root filesystem")
        require(errors, "ALL" in nested(security, "capabilities", "drop", default=[]), f"{name}: Qdrant must drop all Linux capabilities")
        require(errors, bool(nested(qdrant, "resources", "requests")), f"{name}: Qdrant must set resource requests")
        require(errors, bool(nested(qdrant, "resources", "limits")), f"{name}: Qdrant must set resource limits")
        mounts = {mount.get("name"): mount for mount in qdrant.get("volumeMounts", [])}
        require(errors, "storage" in mounts, f"{name}: Qdrant must mount writable storage")
        require(errors, "snapshots" in mounts, f"{name}: Qdrant must mount writable snapshots storage")
        require(errors, "tmp" in mounts and mounts["tmp"].get("mountPath") == "/tmp", f"{name}: Qdrant must mount writable tmp storage")
        ports = {port.get("name") for port in qdrant.get("ports", [])}
        require(errors, {"http", "grpc"} <= ports, f"{name}: Qdrant must expose HTTP and gRPC ports")
    require(errors, bool(find_kind(docs, "Service")), f"{name}: Qdrant chart must render a Service")
    require(errors, bool(find_kind(docs, "ServiceAccount")), f"{name}: Qdrant chart must render a ServiceAccount")
    service_accounts = find_kind(docs, "ServiceAccount")
    if service_accounts:
        require(errors, service_accounts[0].get("automountServiceAccountToken") is False, f"{name}: Qdrant ServiceAccount must disable token automount")
    require(errors, bool(find_kind(docs, "NetworkPolicy")), f"{name}: Qdrant chart must render a NetworkPolicy")
    require(errors, bool(find_kind(docs, "PodDisruptionBudget")), f"{name}: Qdrant chart must render a PodDisruptionBudget")
    deployments = find_kind(docs, "Deployment")
    if deployments:
        volumes = {volume.get("name") for volume in deployments[0]["spec"]["template"]["spec"].get("volumes", [])}
        require(errors, {"storage", "snapshots", "tmp"} <= volumes, f"{name}: Qdrant must define storage, snapshots, and tmp volumes")
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
            "OTEL_TRACING_ENABLED",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "OTEL_SERVICE_NAME",
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
    require(errors, vllm_security.get("readOnlyRootFilesystem") is True, f"{name}: vLLM must use a read-only root filesystem")
    require(errors, "ALL" in nested(vllm_security, "capabilities", "drop", default=[]), f"{name}: vLLM must drop all Linux capabilities")
    require(errors, resource_name in requests, f"{name}: vLLM requests must include {resource_name}")
    require(errors, resource_name in limits, f"{name}: vLLM limits must include {resource_name}")
    env = {item.get("name"): item.get("value") for item in vllm.get("env", [])}
    require(errors, env.get("HF_HOME") == "/models", f"{name}: vLLM must redirect HF_HOME to writable model cache")
    require(errors, env.get("TRANSFORMERS_CACHE") == "/models", f"{name}: vLLM must redirect TRANSFORMERS_CACHE to writable model cache")
    require(errors, env.get("VLLM_CACHE_ROOT") == "/models", f"{name}: vLLM must redirect VLLM_CACHE_ROOT to writable model cache")
    mounts = {mount.get("name"): mount for mount in vllm.get("volumeMounts", [])}
    require(errors, "model-cache" in mounts and mounts["model-cache"].get("mountPath") == "/models", f"{name}: vLLM must mount writable model cache")
    require(errors, "tmp" in mounts and mounts["tmp"].get("mountPath") == "/tmp", f"{name}: vLLM must mount writable tmp storage")
    volumes = {volume.get("name") for volume in pod_spec.get("volumes", [])}
    require(errors, {"model-cache", "tmp"} <= volumes, f"{name}: vLLM must define model-cache and tmp volumes")
    require(errors, bool(find_kind(docs, "ServiceAccount")), f"{name}: vLLM chart must render a ServiceAccount")
    require(errors, bool(find_kind(docs, "PodDisruptionBudget")), f"{name}: vLLM chart must render a PodDisruptionBudget")
    require(errors, bool(find_kind(docs, "HorizontalPodAutoscaler")) or bool(find_kind(docs, "ScaledObject")), f"{name}: vLLM profile must render autoscaling (HPA or a KEDA ScaledObject on a GPU/queue signal)")
    # A GPU server must not scale on CPU utilization (the wrong signal: CPU% never
    # reflects GPU saturation, so the HPA never scales when it should). Use KEDA's
    # request-queue ScaledObject instead.
    for hpa in find_kind(docs, "HorizontalPodAutoscaler"):
        spec = hpa.get("spec", {})
        cpu_targeted = spec.get("targetCPUUtilizationPercentage") is not None or any(
            nested(metric, "resource", "name") == "cpu" for metric in spec.get("metrics", [])
        )
        require(errors, not cpu_targeted, f"{name}: vLLM must not autoscale on CPU (wrong signal for a GPU server); use the KEDA queue ScaledObject")


def check_model_catalog(errors: list[str]) -> None:
    catalog_path = ROOT / "platform/model-catalog/models.yaml"
    require(errors, catalog_path.exists(), "model catalog must exist at platform/model-catalog/models.yaml")
    if not catalog_path.exists():
        return
    catalog = yaml.safe_load(catalog_path.read_text())
    models = nested(catalog, "spec", "models", default=[])
    model_ids = {model.get("id") for model in models}
    approved_runtimes = {model.get("runtime") for model in models if model.get("status") == "approved"}
    require(errors, "ollama" in approved_runtimes, "model catalog must include at least one approved ollama model for the local smoke")
    require(errors, "vllm" in approved_runtimes, "model catalog must include at least one approved vllm model for the customer GPU profile")
    for model in models:
        model_id = model.get("id", "<unknown>")
        require(errors, bool(model.get("owner")), f"model catalog entry {model_id} must define owner")
        require(errors, model.get("status") in {"proposed", "approved", "deprecated", "blocked"}, f"model catalog entry {model_id} must define a valid lifecycle status")
        require(errors, isinstance(model.get("contextWindow"), int) and model.get("contextWindow") > 0, f"model catalog entry {model_id} must define a positive contextWindow")
    for environment in ("local", "customer"):
        values = yaml.safe_load((ROOT / f"deploy/clusters/{environment}/values/inference-gateway.yaml").read_text())
        for model_id in nested(values, "runtime", "allowedModels", default=[]):
            require(errors, model_id in model_ids, f"{environment}: allowed model {model_id} is missing from model catalog")
    configmap = ROOT / "platform/model-catalog/k8s/configmap.yaml"
    require(errors, configmap.exists(), "model catalog ConfigMap must exist")
    if configmap.exists():
        docs = load_yaml_documents(configmap)
        require(errors, bool(find_kind(docs, "ConfigMap")), "model catalog ConfigMap must render as a ConfigMap")


def check_model_governance(errors: list[str]) -> None:
    script = ROOT / "scripts/model-catalog.py"
    require(errors, os.access(script, os.X_OK), "scripts/model-catalog.py must be executable")
    require(errors, (ROOT / "runbooks/model-governance.md").exists(), "model governance runbook must exist")
    require(errors, (ROOT / "results/model-catalog/sample-summary.md").exists(), "model catalog sample summary must exist")
    catalog_models = nested(yaml.safe_load((ROOT / "platform/model-catalog/models.yaml").read_text()), "spec", "models", default=[])
    for model in catalog_models:
        if model.get("status") == "approved":
            promotion_request = model.get("promotionRequest")
            require(errors, bool(promotion_request) and (ROOT / str(promotion_request)).exists(), f"approved model {model.get('id')} must reference an existing promotion request")


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
    require(errors, coding_agent_suite.exists(), "coding-agent eval suite must exist at platform/evals/coding-agent-suite.yaml")
    require(errors, (ROOT / "results/evals/sample-coding-agent-summary.md").exists(), "coding-agent eval sample summary must exist")
    require(errors, (ROOT / "results/evals/sample-coding-agent-summary.json").exists(), "coding-agent eval sample JSON must exist")
    if coding_agent_suite.exists():
        suite = yaml.safe_load(coding_agent_suite.read_text())
        cases = nested(suite, "spec", "cases", default=[])
        require(errors, suite.get("kind") == "EvalSuite", "coding-agent eval suite must use kind EvalSuite")
        require(errors, isinstance(cases, list) and len(cases) >= 4, "coding-agent eval suite must define at least four cases")
        require(errors, "forbiddenAny" in coding_agent_suite.read_text(), "coding-agent eval suite must include forbiddenAny secret-leak checks")


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
    if spec.exists():
        onboarding = yaml.safe_load(spec.read_text())
        require(errors, nested(onboarding, "spec", "tenant", "namespace") == "ai-coding-agents", "tenant onboarding spec should define the coding-agent namespace")
        require(errors, nested(onboarding, "spec", "agentWorkspace", "enabled") is True, "tenant onboarding spec should enable agent workspace output")
        require(errors, bool(nested(onboarding, "spec", "network", "allowedEgressCidrs", default=[])), "tenant onboarding spec should include an approved external egress example")
    if regulated_spec.exists():
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
            require(errors, security.get("allowPrivilegeEscalation") is False, "restore-drill CronJob must block privilege escalation")
            require(errors, security.get("readOnlyRootFilesystem") is True, "restore-drill CronJob must use a read-only root filesystem")
            require(errors, "ALL" in nested(security, "capabilities", "drop", default=[]), "restore-drill CronJob must drop all Linux capabilities")
            require(errors, nested(mounts, "config", "readOnly") is True, "restore-drill config mount must be read-only")
            require(errors, nested(mounts, "fixtures", "readOnly") is True, "restore-drill fixtures mount must be read-only")
            require(errors, "reports" in mounts, "restore-drill CronJob must mount writable reports storage")
            require(errors, "tmp" in mounts and mounts["tmp"].get("mountPath") == "/tmp", "restore-drill CronJob must mount writable tmp storage")
        volumes = {volume.get("name") for volume in pod_spec.get("volumes", [])}
        require(errors, {"config", "reports", "fixtures", "tmp"} <= volumes, "restore-drill CronJob must define config, reports, fixtures, and tmp volumes")

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
            require(errors, security.get("allowPrivilegeEscalation") is False, "sandbox trace smoke Job must block privilege escalation")
            require(errors, security.get("readOnlyRootFilesystem") is True, "sandbox trace smoke Job must use a read-only root filesystem")
            require(errors, "ALL" in nested(security, "capabilities", "drop", default=[]), "sandbox trace smoke Job must drop all Linux capabilities")
            require(errors, "tmp" in mounts and mounts["tmp"].get("mountPath") == "/tmp", "sandbox trace smoke Job must mount writable tmp storage")
        volumes = {volume.get("name") for volume in pod_spec.get("volumes", [])}
        require(errors, "tmp" in volumes, "sandbox trace smoke Job must define a tmp volume")

    policies = (ROOT / "deploy/policies/kyverno/policies.yaml").read_text()
    require(errors, "require-read-only-root-filesystem" in policies, "Kyverno restricted policy must require read-only root filesystems")
    kyverno_tests = (ROOT / "deploy/policies/kyverno/tests/kyverno-test.yaml").read_text()
    require(errors, "writable-root-pod" in kyverno_tests, "Kyverno tests must cover read-only root filesystem enforcement")


def check_evidence_pack(errors: list[str]) -> None:
    require(errors, os.access(ROOT / "scripts/evidence-pack.py", os.X_OK), "scripts/evidence-pack.py must be executable")
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


def check_release_packaging(errors: list[str]) -> None:
    release_version = latest_changelog_version(errors)
    release_tag = f"v{release_version}" if release_version else ""
    workflow_path = ROOT / ".github/workflows/ci.yml"
    require(errors, workflow_path.exists(), "CI workflow must exist")
    if workflow_path.exists():
        workflow = workflow_path.read_text()
        for token in (
            "GATEWAY_TAGS",
            "RAG_TAGS",
            "GITHUB_REF_NAME",
            "steps.build_gateway.outputs.digest",
            "steps.build_rag.outputs.digest",
            "cosign sign --yes",
            "actions/attest-build-provenance@",
            "actions/attest@",
            "attestations: write",
            "push-to-registry: true",
            "supply-chain-checksums.txt",
            "gh release create",
            "gh release upload",
            "--generate-notes",
            "severity: HIGH,CRITICAL",
            'exit-code: "1"',
            "actions/upload-artifact",
        ):
            require(errors, token in workflow, f"CI workflow must publish and sign release supply-chain evidence with {token}")
    scorecard_path = ROOT / ".github/workflows/scorecard.yml"
    require(errors, scorecard_path.exists(), "OpenSSF Scorecard workflow must exist")
    if scorecard_path.exists():
        scorecard = scorecard_path.read_text()
        for token in (
            "ossf/scorecard-action@",
            "results_format: sarif",
            "publish_results: false",
            "github/codeql-action/upload-sarif",
            "security-events: write",
            "id-token: write",
        ):
            require(errors, token in scorecard, f"OpenSSF Scorecard workflow missing {token}")

    gateway_values = yaml.safe_load((ROOT / "deploy/charts/inference-gateway/values.yaml").read_text()) or {}
    rag_values = yaml.safe_load((ROOT / "deploy/charts/rag-service/values.yaml").read_text()) or {}
    gateway_chart = yaml.safe_load((ROOT / "deploy/charts/inference-gateway/Chart.yaml").read_text()) or {}
    rag_chart = yaml.safe_load((ROOT / "deploy/charts/rag-service/Chart.yaml").read_text()) or {}
    gateway_tag = str(nested(gateway_values, "image", "tag", default=""))
    rag_tag = str(nested(rag_values, "image", "tag", default=""))
    gateway_version = str(gateway_chart.get("version", ""))
    rag_version = str(rag_chart.get("version", ""))
    require(errors, gateway_version == release_version, "inference-gateway chart version must match latest CHANGELOG version")
    require(errors, rag_version == release_version, "rag-service chart version must match latest CHANGELOG version")
    require(errors, gateway_tag.startswith("v"), "inference-gateway chart image tag must be a release tag")
    require(errors, rag_tag.startswith("v"), "rag-service chart image tag must be a release tag")
    require(errors, gateway_tag.lstrip("v") == gateway_version, "inference-gateway chart version must match image tag")
    require(errors, rag_tag.lstrip("v") == rag_version, "rag-service chart version must match image tag")
    require(errors, gateway_tag == release_tag, "inference-gateway image tag must match latest CHANGELOG release tag")
    require(errors, rag_tag == release_tag, "rag-service image tag must match latest CHANGELOG release tag")
    require(errors, gateway_tag not in {"latest", "main"}, "inference-gateway chart must not default to floating tags")
    require(errors, rag_tag not in {"latest", "main"}, "rag-service chart must not default to floating tags")
    require(errors, str(nested(gateway_values, "image", "repository", default="")).startswith("ghcr.io/"), "inference-gateway chart must default to a GHCR image")
    require(errors, str(nested(rag_values, "image", "repository", default="")).startswith("ghcr.io/"), "rag-service chart must default to a GHCR image")

    for chart in ("agent-workspace", "budget-redis", "inference-gateway", "ollama", "qdrant-vector-store", "rag-service", "vllm"):
        metadata = yaml.safe_load((ROOT / f"deploy/charts/{chart}/Chart.yaml").read_text()) or {}
        require(errors, metadata.get("version") == gateway_version, f"{chart} chart version must match the release chart version")
        if chart in {"agent-workspace", "inference-gateway", "rag-service"}:
            require(errors, str(metadata.get("appVersion")) == release_version, f"{chart} appVersion must match latest CHANGELOG version")

    for path in ("README.md", "docs/getting-started.md", "deploy/clusters/customer/README.md"):
        text = (ROOT / path).read_text()
        require(errors, f"CUSTOMER_REVISION={release_tag}" in text, f"{path} must show CUSTOMER_REVISION={release_tag}")

    index_text = (ROOT / "docs/index.md").read_text()
    require(
        errors,
        f"Current release `{release_tag}`" in index_text,
        f"docs/index.md Maturity admonition must show Current release {release_tag}",
    )

    for path in ("src/inference-gateway/app/main.py", "src/rag-service/app/main.py"):
        text = (ROOT / path).read_text()
        require(errors, f'SERVICE_VERSION = "{release_version}"' in text, f"{path} SERVICE_VERSION must match latest CHANGELOG version")

    for path in ("platform/api-contracts/inference-gateway.openapi.json", "platform/api-contracts/rag-service.openapi.json"):
        api = load_json(ROOT / path)
        require(errors, nested(api, "info", "version") == release_version, f"{path} info.version must match latest CHANGELOG version")

    for service in ("inference-gateway", "rag-service"):
        dockerignore = ROOT / f"src/{service}/.dockerignore"
        dockerfile = ROOT / f"src/{service}/Dockerfile"
        requirements = ROOT / f"src/{service}/requirements.txt"
        dev_requirements = ROOT / f"src/{service}/requirements-dev.txt"
        runtime_lock = ROOT / f"src/{service}/requirements.lock"
        dev_lock = ROOT / f"src/{service}/requirements-dev.lock"
        require(errors, dockerfile.exists(), f"{service} Dockerfile must exist")
        if dockerfile.exists():
            dockerfile_text = dockerfile.read_text()
            require(errors, "python:3.14-alpine@sha256:" in dockerfile_text, f"{service} Dockerfile must use a pinned Alpine Python base image")
            require(errors, "python:3.14-slim" not in dockerfile_text, f"{service} Dockerfile must not use Debian slim runtime base")
            require(errors, "COPY requirements.lock ." in dockerfile_text, f"{service} Dockerfile must copy the hashed runtime lockfile")
            require(errors, "--require-hashes -r requirements.lock" in dockerfile_text, f"{service} Dockerfile must install runtime dependencies with hash checking")
        require(errors, requirements.exists(), f"{service} runtime requirements must exist")
        runtime_pins: dict[str, str] = {}
        if requirements.exists():
            requirements_text = requirements.read_text()
            runtime_pins = requirement_pins(requirements)
            require(errors, "pytest" not in requirements_text, f"{service} runtime requirements must not include pytest")
            require(errors, runtime_pins, f"{service} runtime requirements must pin dependencies with == versions")
        require(errors, dev_requirements.exists(), f"{service} dev requirements must exist")
        if dev_requirements.exists():
            dev_text = dev_requirements.read_text()
            dev_pins = requirement_pins(dev_requirements)
            require(errors, "-r requirements.txt" in dev_text and "pytest" in dev_text, f"{service} dev requirements must extend runtime requirements and include pytest")
            require(errors, dev_pins, f"{service} dev requirements must pin dependencies with == versions")
        require(errors, runtime_lock.exists(), f"{service} runtime lockfile must exist")
        if runtime_lock.exists():
            require(errors, "--hash=sha256:" in runtime_lock.read_text(), f"{service} runtime lockfile must include package hashes")
            require_lock_contains_pins(errors, requirements, runtime_lock, runtime_pins)
        require(errors, dev_lock.exists(), f"{service} dev lockfile must exist")
        if dev_lock.exists():
            dev_lock_text = dev_lock.read_text()
            require(errors, "--hash=sha256:" in dev_lock_text, f"{service} dev lockfile must include package hashes")
            require_lock_contains_pins(errors, requirements, dev_lock, runtime_pins)
            if dev_requirements.exists():
                require_lock_contains_pins(errors, dev_requirements, dev_lock, requirement_pins(dev_requirements))
        require(errors, dockerignore.exists(), f"{service} Docker context must define .dockerignore")
        if dockerignore.exists():
            text = dockerignore.read_text()
            require(errors, ".venv/" in text and ".pytest_cache/" in text, f"{service} .dockerignore must exclude local test environments")

    for script in ("scripts/bootstrap-python.sh", "scripts/test-gateway.sh", "scripts/test-rag.sh"):
        text = (ROOT / script).read_text()
        require(errors, "--require-hashes -r requirements-dev.lock" in text, f"{script} must install hashed dev dependencies")
        require(errors, "install --upgrade pip" not in text, f"{script} must not upgrade pip from an unpinned network dependency")

    image_scan = ROOT / "scripts/image-scan.sh"
    supply_chain_evidence = ROOT / "scripts/supply-chain-evidence.py"
    require(errors, os.access(image_scan, os.X_OK), "scripts/image-scan.sh must be executable")
    require(errors, os.access(supply_chain_evidence, os.X_OK), "scripts/supply-chain-evidence.py must be executable")
    if image_scan.exists():
        image_scan_text = image_scan.read_text()
        for token in ("SYFT_BIN", "spdx-json", "--format sarif", "supply-chain-checksums", "results/supply-chain"):
            require(errors, token in image_scan_text, f"scripts/image-scan.sh must generate local supply-chain evidence with {token}")
        require(errors, "scripts/supply-chain-evidence.py --summary" in image_scan_text, "scripts/image-scan.sh must validate generated supply-chain evidence")
    if supply_chain_evidence.exists():
        supply_chain_text = supply_chain_evidence.read_text()
        for token in ("validate_sbom", "validate_sarif", "parse_checksums", "sha256", "strict-current"):
            require(errors, token in supply_chain_text, f"supply-chain evidence validator must enforce {token}")
    makefile = (ROOT / "Makefile").read_text()
    require(errors, "image-scan:" in makefile, "Makefile must expose image-scan target")
    require(errors, "supply-chain-check:" in makefile, "Makefile must expose supply-chain-check target")

    overlay_script = ROOT / "scripts/configure-customer-overlay.py"
    require(errors, os.access(overlay_script, os.X_OK), "customer overlay configurator must be executable")
    makefile = (ROOT / "Makefile").read_text()
    require(errors, "customer-overlay:" in makefile and "customer-overlay-check:" in makefile, "Makefile must expose customer overlay targets")
    customer_readme = (ROOT / "deploy/clusters/customer/README.md").read_text()
    require(errors, "make customer-overlay" in customer_readme, "customer README must document overlay configuration")
    require(errors, "Handoff Checklist" in customer_readme, "customer README must include a handoff checklist")


def check_release_gates(errors: list[str]) -> None:
    config_path = ROOT / "platform/slo/release-gates.yaml"
    script = ROOT / "scripts/release-gate.py"
    require(errors, config_path.exists(), "release gate config must exist at platform/slo/release-gates.yaml")
    require(errors, os.access(script, os.X_OK), "scripts/release-gate.py must be executable")
    require(errors, (ROOT / "runbooks/release-gates.md").exists(), "release gates runbook must exist")
    require(errors, (ROOT / "results/release-gate/sample-summary.md").exists(), "release gate sample summary must exist")
    makefile = (ROOT / "Makefile").read_text()
    require(errors, "release-gate-strict:" in makefile, "Makefile must expose release-gate-strict")
    require(errors, "release-report-strict:" in makefile, "Makefile must expose release-report-strict")
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
        expected = {"eval", "load", "restore", "toolchain", "egress", "retention", "slo", "quota", "modelProvenance", "supplyChain", "evidencePack"}
        require(errors, expected <= gates, f"release gate config missing {sorted(expected - gates)}")
    if script.exists():
        source = script.read_text()
        require(errors, "--require-current-evidence" in source, "release gate script must support current-evidence enforcement")
        require(errors, "--max-evidence-age-hours" in source, "release gate script must support evidence freshness enforcement")
        require(errors, "check_supply_chain" in source and "supply-chain-evidence.py" in source, "release gate script must validate supply-chain evidence")


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
        expected = {"inference-availability", "inference-latency", "eval-quality-smoke", "restore-verification", "agent-platform-readiness"}
        require(errors, expected <= objective_ids, f"SLO objective config missing {sorted(expected - objective_ids)}")
    alerts = (ROOT / "deploy/observability/alerts/ai-platform-alerts.yaml").read_text()
    for alert in ("InferenceGatewayErrorBudgetFastBurn", "InferenceGatewayErrorBudgetSlowBurn", "InferenceGatewayHighLatency", "RestoreDrillFailed"):
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
        required_labels = {"platform.ai/owner", "platform.ai/cost-center", "platform.ai/environment", "platform.ai/sandbox-id"}
        require(errors, required_labels <= labels, f"quota plan policy missing chargeback labels {sorted(required_labels - labels)}")


def check_model_provenance_governance(errors: list[str]) -> None:
    policy_path = ROOT / "platform/governance/model-provenance.yaml"
    script = ROOT / "scripts/model-provenance.py"
    require(errors, policy_path.exists(), "model provenance policy must exist at platform/governance/model-provenance.yaml")
    require(errors, os.access(script, os.X_OK), "scripts/model-provenance.py must be executable")
    require(errors, (ROOT / "runbooks/model-provenance.md").exists(), "model provenance runbook must exist")
    require(errors, (ROOT / "results/model-provenance/sample-summary.md").exists(), "model provenance sample summary must exist")
    require(errors, (ROOT / "results/model-provenance/sample-summary.json").exists(), "model provenance sample JSON must exist")
    if policy_path.exists():
        policy = yaml.safe_load(policy_path.read_text()) or {}
        require(errors, policy.get("kind") == "ModelProvenanceSet", "model provenance policy kind must be ModelProvenanceSet")
        artifacts = nested(policy, "spec", "artifacts", default=[])
        model_ids = {item.get("modelId") for item in artifacts if isinstance(item, dict)}
        catalog_models = nested(yaml.safe_load((ROOT / "platform/model-catalog/models.yaml").read_text()), "spec", "models", default=[])
        expected = {model.get("id") for model in catalog_models if model.get("status") == "approved"}
        require(errors, expected <= model_ids, f"model provenance must cover all approved catalog models; missing {sorted(expected - model_ids)}")
        required = set(nested(policy, "spec", "requiredEvidence", default=[]))
        required_fields = {"sourceUri", "immutableRef", "digest", "license", "dataClassification", "riskTier", "promotionRequest", "servingProfiles"}
        require(errors, required_fields <= required, f"model provenance policy missing required evidence {sorted(required_fields - required)}")


def check_egress_governance(errors: list[str]) -> None:
    catalog_path = ROOT / "platform/network/egress-catalog.yaml"
    script = ROOT / "scripts/egress-governance.py"
    require(errors, catalog_path.exists(), "egress governance catalog must exist at platform/network/egress-catalog.yaml")
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
        require(errors, policy.get("kind") == "DataRetentionPolicy", "data retention policy kind must be DataRetentionPolicy")
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
        rag_values_path = ROOT / f"deploy/clusters/{environment}/values/rag-service.yaml"
        qdrant_values_path = ROOT / f"deploy/clusters/{environment}/values/qdrant-vector-store.yaml"
        agent_values_path = ROOT / f"deploy/clusters/{environment}/values/agent-workspace.yaml"
        require(errors, rag_values_path.exists(), f"{environment}: RAG service values must exist")
        require(errors, qdrant_values_path.exists(), f"{environment}: Qdrant vector-store values must exist")
        require(errors, agent_values_path.exists(), f"{environment}: agent workspace values must exist")
        if rag_values_path.exists():
            rag_values = yaml.safe_load(rag_values_path.read_text())
            require(errors, nested(rag_values, "traceability", "defaultSandboxId") is not None, f"{environment}: RAG values must define traceability.defaultSandboxId")
            expected_backend = "lexical" if environment == "local" else "qdrant"
            require(errors, nested(rag_values, "retrieval", "backend") == expected_backend, f"{environment}: RAG retrieval.backend should be {expected_backend}")
            require(errors, nested(rag_values, "retrieval", "vectorStore", "collection") is not None, f"{environment}: RAG vectorStore.collection must be set")
            require(errors, nested(rag_values, "retrieval", "vectorStore", "collectionVersion") is not None, f"{environment}: RAG vectorStore.collectionVersion must be set")
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
    require(errors, production_doc.startswith("# Production Readiness Matrix"), "production readiness document must keep its title")
    require(errors, "## Required Controls" in production_doc, "production readiness document must list required controls")
    require(errors, "| Area |" in production_doc and "| Validation |" in production_doc, "production readiness controls must remain tabular")
    policies = (ROOT / "deploy/policies/kyverno/policies.yaml").read_text()
    require(errors, "platform.ai/sandbox-id" in policies, "Kyverno policies must require sandbox id labels")
    require(errors, os.access(ROOT / "scripts/sandbox-smoke.sh", os.X_OK), "scripts/sandbox-smoke.sh must be executable")
    require(errors, os.access(ROOT / "scripts/rag-smoke.sh", os.X_OK), "scripts/rag-smoke.sh must be executable")
    require(errors, os.access(ROOT / "scripts/agent-lab-up.sh", os.X_OK), "scripts/agent-lab-up.sh must be executable")
    require(errors, os.access(ROOT / "scripts/agent-smoke.sh", os.X_OK), "scripts/agent-smoke.sh must be executable")
    require(errors, os.access(ROOT / "scripts/loadtest-local.sh", os.X_OK), "scripts/loadtest-local.sh must be executable")
    require(errors, (ROOT / "loadtest/mock-runtime.py").exists(), "local load test mock runtime must exist")
    loadtest = (ROOT / "loadtest/chat-completions.js").read_text()
    require(errors, "summaryTrendStats" in loadtest and "p(99)" in loadtest, "load test must export p99 latency evidence")
    loadtest_local = (ROOT / "scripts/loadtest-local.sh").read_text()
    for phrase in ("loadtest/mock-runtime.py", "uvicorn app.main:app", "API_KEY_AUTH_ENABLED=true", "k6 run --summary-export"):
        require(errors, phrase in loadtest_local, f"local load test harness missing {phrase}")


def main() -> int:
    errors: list[str] = []
    try:
        check_agent_workspace_render("agent-workspace-defaults", render_chart("agent-workspace"), errors)
        for environment in ("local", "customer"):
            check_agent_workspace_render(
                f"{environment}-agent-workspace",
                render_chart("agent-workspace", ROOT / f"deploy/clusters/{environment}/values/agent-workspace.yaml"),
                errors,
            )
        check_budget_redis_render(render_chart("budget-redis"), errors)
        check_ollama_render("ollama-defaults", render_chart("ollama"), errors)
        check_ollama_render(
            "local-ollama",
            render_chart("ollama", ROOT / "deploy/clusters/local/values/ollama.yaml"),
            errors,
        )
        check_qdrant_render("qdrant-defaults", render_chart("qdrant-vector-store"), True, errors)
        check_qdrant_render(
            "local-qdrant-vector-store",
            render_chart("qdrant-vector-store", ROOT / "deploy/clusters/local/values/qdrant-vector-store.yaml"),
            False,
            errors,
        )
        check_qdrant_render(
            "customer-qdrant-vector-store",
            render_chart("qdrant-vector-store", ROOT / "deploy/clusters/customer/values/qdrant-vector-store.yaml"),
            True,
            errors,
        )
        check_gateway_render("chart-defaults", render_chart("inference-gateway"), errors)
        for environment in ("local", "customer"):
            check_gateway_render(
                environment,
                render_chart("inference-gateway", ROOT / f"deploy/clusters/{environment}/values/inference-gateway.yaml"),
                errors,
            )
        check_rag_render("chart-defaults", render_chart("rag-service"), False, errors)
        check_rag_render(
            "local-rag-service",
            render_chart("rag-service", ROOT / "deploy/clusters/local/values/rag-service.yaml"),
            False,
            errors,
        )
        check_rag_render(
            "customer-rag-service",
            render_chart("rag-service", ROOT / "deploy/clusters/customer/values/rag-service.yaml"),
            True,
            errors,
        )
        # The default profile is rendered too: it is a complete, applyable overlay,
        # so its autoscaler must be gated like the vendor profiles (no CPU HPA).
        check_vllm_render(
            "customer-vllm-default",
            render_chart("vllm", ROOT / "deploy/clusters/customer/values/vllm.yaml"),
            "nvidia.com/gpu",
            errors,
        )
        check_vllm_render(
            "customer-vllm-nvidia",
            render_chart("vllm", ROOT / "deploy/clusters/customer/values/vllm-nvidia.yaml"),
            "nvidia.com/gpu",
            errors,
        )
        check_vllm_render(
            "customer-vllm-amd",
            render_chart("vllm", ROOT / "deploy/clusters/customer/values/vllm-amd.yaml"),
            "amd.com/gpu",
            errors,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        errors.append(f"failed to render production charts: {exc}")

    check_sandbox(errors)
    check_platform_namespace_psa(errors)
    check_model_catalog(errors)
    check_model_governance(errors)
    check_evals(errors)
    check_tenant_labs(errors)
    check_tenant_onboarding(errors)
    check_chaos_drills(errors)
    check_static_workload_security(errors)
    check_evidence_pack(errors)
    check_validation_toolchain(errors)
    check_release_gates(errors)
    check_release_packaging(errors)
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
