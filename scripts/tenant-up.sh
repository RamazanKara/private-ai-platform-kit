#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"

require_cmd kubectl "kubectl is required to provision tenant lab namespaces."

TENANT_ID="${TENANT_ID:-team-a-lab}"
TENANT_NAME="${TENANT_NAME:-${TENANT_ID%-lab}}"
TENANT_NAMESPACE="${TENANT_NAMESPACE:-ai-${TENANT_ID}}"
TENANT_OWNER="${TENANT_OWNER:-${TENANT_NAME}}"
TENANT_GROUP="${TENANT_GROUP:-${TENANT_NAME}}"
COST_CENTER="${COST_CENTER:-research}"
ENVIRONMENT="${ENVIRONMENT:-local}"
REQUEST_CPU="${REQUEST_CPU:-2}"
REQUEST_MEMORY="${REQUEST_MEMORY:-4Gi}"
LIMIT_CPU="${LIMIT_CPU:-4}"
LIMIT_MEMORY="${LIMIT_MEMORY:-8Gi}"
PODS="${PODS:-10}"

validate_k8s_name "$TENANT_ID" "TENANT_ID"
validate_k8s_name "$TENANT_NAMESPACE" "TENANT_NAMESPACE"
validate_k8s_name "$TENANT_NAME" "TENANT_NAME"

log "applying tenant namespace ${TENANT_NAMESPACE} for sandbox ${TENANT_ID}"
kubectl apply -f - <<YAML
apiVersion: v1
kind: Namespace
metadata:
  name: ${TENANT_NAMESPACE}
  labels:
    app.kubernetes.io/name: ${TENANT_NAMESPACE}
    app.kubernetes.io/part-of: private-ai-platform-kit
    platform.ai/cost-center: ${COST_CENTER}
    platform.ai/environment: ${ENVIRONMENT}
    platform.ai/owner: ${TENANT_OWNER}
    platform.ai/sandbox-id: ${TENANT_ID}
    platform.ai/tenant: ${TENANT_NAME}
    platform.ai/traceable-sandbox: "true"
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/warn: restricted
---
apiVersion: v1
kind: ResourceQuota
metadata:
  name: tenant-quota
  namespace: ${TENANT_NAMESPACE}
  labels:
    app.kubernetes.io/name: tenant-quota
    app.kubernetes.io/part-of: private-ai-platform-kit
    platform.ai/cost-center: ${COST_CENTER}
    platform.ai/environment: ${ENVIRONMENT}
    platform.ai/owner: ${TENANT_OWNER}
    platform.ai/sandbox-id: ${TENANT_ID}
    platform.ai/tenant: ${TENANT_NAME}
spec:
  hard:
    requests.cpu: "${REQUEST_CPU}"
    requests.memory: ${REQUEST_MEMORY}
    limits.cpu: "${LIMIT_CPU}"
    limits.memory: ${LIMIT_MEMORY}
    pods: "${PODS}"
    configmaps: "20"
    secrets: "10"
    persistentvolumeclaims: "2"
---
apiVersion: v1
kind: LimitRange
metadata:
  name: tenant-defaults
  namespace: ${TENANT_NAMESPACE}
  labels:
    app.kubernetes.io/name: tenant-defaults
    app.kubernetes.io/part-of: private-ai-platform-kit
    platform.ai/cost-center: ${COST_CENTER}
    platform.ai/environment: ${ENVIRONMENT}
    platform.ai/owner: ${TENANT_OWNER}
    platform.ai/sandbox-id: ${TENANT_ID}
    platform.ai/tenant: ${TENANT_NAME}
spec:
  limits:
    - type: Container
      defaultRequest:
        cpu: 50m
        memory: 64Mi
      default:
        cpu: 500m
        memory: 512Mi
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: tenant-default-deny
  namespace: ${TENANT_NAMESPACE}
  labels:
    app.kubernetes.io/name: tenant-default-deny
    app.kubernetes.io/part-of: private-ai-platform-kit
    platform.ai/cost-center: ${COST_CENTER}
    platform.ai/environment: ${ENVIRONMENT}
    platform.ai/owner: ${TENANT_OWNER}
    platform.ai/sandbox-id: ${TENANT_ID}
    platform.ai/tenant: ${TENANT_NAME}
spec:
  podSelector: {}
  policyTypes:
    - Ingress
    - Egress
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: tenant-allow-dns-and-gateway
  namespace: ${TENANT_NAMESPACE}
  labels:
    app.kubernetes.io/name: tenant-allow-dns-and-gateway
    app.kubernetes.io/part-of: private-ai-platform-kit
    platform.ai/cost-center: ${COST_CENTER}
    platform.ai/environment: ${ENVIRONMENT}
    platform.ai/owner: ${TENANT_OWNER}
    platform.ai/sandbox-id: ${TENANT_ID}
    platform.ai/tenant: ${TENANT_NAME}
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: inference
      ports:
        - protocol: TCP
          port: 8080
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: rag
      ports:
        - protocol: TCP
          port: 8080
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: tenant-trace-contract
  namespace: ${TENANT_NAMESPACE}
  labels:
    app.kubernetes.io/name: tenant-trace-contract
    app.kubernetes.io/part-of: private-ai-platform-kit
    platform.ai/cost-center: ${COST_CENTER}
    platform.ai/environment: ${ENVIRONMENT}
    platform.ai/owner: ${TENANT_OWNER}
    platform.ai/sandbox-id: ${TENANT_ID}
    platform.ai/tenant: ${TENANT_NAME}
data:
  sandbox-id: ${TENANT_ID}
  required-headers: X-Request-ID, X-Sandbox-ID, X-API-Key, traceparent
  gateway-url: http://inference-gateway-inference-gateway.inference.svc.cluster.local:8080
  rag-url: http://rag-service-rag-service.rag.svc.cluster.local:8080
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: tenant-lab-viewer
  namespace: ${TENANT_NAMESPACE}
  labels:
    app.kubernetes.io/name: tenant-lab-viewer
    app.kubernetes.io/part-of: private-ai-platform-kit
    platform.ai/cost-center: ${COST_CENTER}
    platform.ai/environment: ${ENVIRONMENT}
    platform.ai/owner: ${TENANT_OWNER}
    platform.ai/sandbox-id: ${TENANT_ID}
    platform.ai/tenant: ${TENANT_NAME}
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/log", "services", "configmaps", "events"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: tenant-lab-viewers
  namespace: ${TENANT_NAMESPACE}
  labels:
    app.kubernetes.io/name: tenant-lab-viewers
    app.kubernetes.io/part-of: private-ai-platform-kit
    platform.ai/cost-center: ${COST_CENTER}
    platform.ai/environment: ${ENVIRONMENT}
    platform.ai/owner: ${TENANT_OWNER}
    platform.ai/sandbox-id: ${TENANT_ID}
    platform.ai/tenant: ${TENANT_NAME}
subjects:
  - kind: Group
    name: ${TENANT_GROUP}
    apiGroup: rbac.authorization.k8s.io
roleRef:
  kind: Role
  name: tenant-lab-viewer
  apiGroup: rbac.authorization.k8s.io
YAML

log "tenant namespace ${TENANT_NAMESPACE} is ready"
