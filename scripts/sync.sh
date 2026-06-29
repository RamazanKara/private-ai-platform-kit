#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
require_cmd kubectl "kubectl is required to sync or inspect Argo CD apps."

# Wait for an Argo CD-managed workload to be created and then roll out. Argo CD
# reconciles asynchronously, so the resource may not exist yet when this is first
# called; poll for creation, then block on the rollout within the same deadline.
wait_for_rollout() {
  local ns="$1" target="$2" timeout="${3:-300}"
  local deadline=$(( SECONDS + timeout ))
  log "waiting for ${ns}/${target} (timeout ${timeout}s)"
  until kubectl -n "$ns" get "$target" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      die "timed out waiting for Argo CD to create ${ns}/${target}"
    fi
    sleep 5
  done
  kubectl -n "$ns" rollout status "$target" --timeout="$(( deadline - SECONDS ))s"
}

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

if [[ "${LOCAL_DIRECT_APPLY:-0}" == "1" ]]; then
  direct_apply_local
  exit 0
fi

ROOT_APP="private-ai-platform-kit-root"

if has_cmd argocd; then
  # Core mode runs the Argo CD CLI directly against the Kubernetes API using the
  # active kubeconfig, so no argocd login, port-forward, or --server address is
  # needed. This is the supported headless path and avoids the
  # "Argo CD server address unspecified" failure when syncing non-interactively.
  export ARGOCD_OPTS="${ARGOCD_OPTS:-} --core"
  argocd app sync "$ROOT_APP" --timeout 600 || true
  argocd app wait "$ROOT_APP" --sync --timeout 300 || true
else
  log "argocd CLI not found; requesting app refresh through kubectl annotations"
  kubectl -n argocd annotate application "$ROOT_APP" argocd.argoproj.io/refresh=hard --overwrite || true
fi

kubectl -n argocd get applications || true

if kubectl -n argocd get application "$ROOT_APP" -o jsonpath='{.status.conditions[*].message}' 2>/dev/null | grep -qi 'Repository not found'; then
  root_app_file="gitops/argocd/root-app.yaml"
  if [[ "${ENVIRONMENT:-local}" == "customer" ]]; then
    root_app_file="gitops/argocd/root-app-customer.yaml and clusters/customer/apps.yaml"
  fi
  log "GitOps repo is not reachable from Argo CD yet; publish the repo or update ${root_app_file}. Running local direct-apply fallback."
  direct_apply_local
  exit 0
fi

# Child apps use automated sync, but Argo CD reconciles asynchronously. Gate on
# the smoke-critical local runtime workloads so the gateway and RAG smoke tests
# do not race the reconcile. (Previously the swallowed argocd CLI errors left no
# effective wait, so the smoke connected before the gateway deployment existed.)
# Generous timeouts: the operator app (Kyverno/KEDA) and the runtime apps
# reconcile in parallel, and Kyverno's Enforce webhook can make Argo CD retry the
# runtime sync on a later reconcile cycle before the workloads appear.
if [[ "${ENVIRONMENT:-local}" != "customer" ]]; then
  wait_for_rollout budget    deploy/budget-redis                        300
  wait_for_rollout ollama    statefulset/ollama                         600
  wait_for_rollout vector    deploy/qdrant-vector-store                 600
  wait_for_rollout inference deploy/inference-gateway-inference-gateway 600
  wait_for_rollout rag       deploy/rag-service-rag-service             600
fi
