#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
require_cmd kubectl "kubectl is required to sync or inspect Argo CD apps."

direct_apply_local() {
  require_cmd helm "Helm is required for the local direct-apply fallback."
  log "directly applying local runtime charts for workstation validation"
  kubectl apply -f "$ROOT/sandbox/base"
  kubectl create namespace ollama --dry-run=client -o yaml | kubectl apply -f -
  kubectl create namespace budget --dry-run=client -o yaml | kubectl apply -f -
  kubectl create namespace inference --dry-run=client -o yaml | kubectl apply -f -
  kubectl create namespace rag --dry-run=client -o yaml | kubectl apply -f -
  kubectl create namespace vector --dry-run=client -o yaml | kubectl apply -f -
  kubectl apply -f "$ROOT/model-catalog/k8s"
  helm upgrade --install budget-redis "$ROOT/charts/budget-redis" \
    --namespace budget
  helm upgrade --install ollama "$ROOT/charts/ollama" \
    --namespace ollama \
    --values "$ROOT/clusters/local/values/ollama.yaml"
  helm upgrade --install inference-gateway "$ROOT/charts/inference-gateway" \
    --namespace inference \
    --values "$ROOT/clusters/local/values/inference-gateway.yaml" \
    --set serviceMonitor.enabled=false \
    --set keda.enabled=false
  helm upgrade --install qdrant-vector-store "$ROOT/charts/qdrant-vector-store" \
    --namespace vector \
    --values "$ROOT/clusters/local/values/qdrant-vector-store.yaml" \
    --set serviceMonitor.enabled=false
  helm upgrade --install rag-service "$ROOT/charts/rag-service" \
    --namespace rag \
    --values "$ROOT/clusters/local/values/rag-service.yaml" \
    --set serviceMonitor.enabled=false \
    --set autoscaling.enabled=false
  helm upgrade --install agent-workspace "$ROOT/charts/agent-workspace" \
    --namespace ai-agents \
    --create-namespace \
    --values "$ROOT/clusters/local/values/agent-workspace.yaml"
  kubectl -n inference rollout restart deploy/inference-gateway-inference-gateway >/dev/null
  kubectl -n vector rollout restart deploy/qdrant-vector-store >/dev/null
  kubectl -n rag rollout restart deploy/rag-service-rag-service >/dev/null
  kubectl -n budget rollout status deploy/budget-redis --timeout=5m
  kubectl -n ollama rollout status statefulset/ollama --timeout=5m || true
  kubectl -n vector rollout status deploy/qdrant-vector-store --timeout=5m
  kubectl -n inference rollout status deploy/inference-gateway-inference-gateway --timeout=5m
  kubectl -n rag rollout status deploy/rag-service-rag-service --timeout=5m
}

if has_cmd argocd; then
  argocd app sync private-ai-platform-kit-root --grpc-web || true
  argocd app wait private-ai-platform-kit-root --health --sync --timeout 600 --grpc-web || true
else
  log "argocd CLI not found; requesting app refresh through kubectl annotations"
  kubectl -n argocd annotate application private-ai-platform-kit-root argocd.argoproj.io/refresh=hard --overwrite
fi

kubectl -n argocd get applications || true

if [[ "${LOCAL_DIRECT_APPLY:-0}" == "1" ]]; then
  direct_apply_local
elif kubectl -n argocd get application private-ai-platform-kit-root -o jsonpath='{.status.conditions[*].message}' 2>/dev/null | grep -qi 'Repository not found'; then
  log "GitOps repo is not reachable from Argo CD yet; publish the repo or update gitops/argocd/root-app.yaml. Running local direct-apply fallback."
  direct_apply_local
fi
