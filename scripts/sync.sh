#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
require_cmd kubectl "kubectl is required to sync or inspect Argo CD apps."

ENVIRONMENT="${ENVIRONMENT:-local}"
CLUSTER_NAME="${CLUSTER_NAME:-private-ai-platform-kit}"
ROOT_APP="private-ai-platform-kit-root"
ARGO_SYNC_TIMEOUT="${ARGO_SYNC_TIMEOUT:-600}"
ARGO_POLL_INTERVAL="${ARGO_POLL_INTERVAL:-5}"

if [[ "$ENVIRONMENT" != "local" && "$ENVIRONMENT" != "customer" ]]; then
  die "ENVIRONMENT must be local or customer (got ${ENVIRONMENT})"
fi
if ! [[ "$ARGO_SYNC_TIMEOUT" =~ ^[1-9][0-9]*$ && "$ARGO_POLL_INTERVAL" =~ ^[1-9][0-9]*$ ]]; then
  die "ARGO_SYNC_TIMEOUT and ARGO_POLL_INTERVAL must be positive integers"
fi

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

assert_local_direct_apply_context() {
  local expected_context="${LOCAL_DIRECT_APPLY_CONTEXT:-kind-${CLUSTER_NAME}}"
  local current_context
  current_context="$(kubectl config current-context)" || die "could not determine the active Kubernetes context"
  if [[ "$current_context" != "$expected_context" ]]; then
    die "refusing local direct apply to context '${current_context}'; expected '${expected_context}'. Set LOCAL_DIRECT_APPLY_CONTEXT explicitly only for an intended local cluster"
  fi
}

ensure_platform_namespace() {
  local namespace="$1"
  kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl label namespace "$namespace" \
    app.kubernetes.io/part-of=private-ai-platform-kit \
    pod-security.kubernetes.io/enforce=restricted \
    pod-security.kubernetes.io/audit=restricted \
    pod-security.kubernetes.io/warn=restricted \
    --overwrite >/dev/null
}

direct_apply_local() {
  if [[ "$ENVIRONMENT" != "local" ]]; then
    die "local direct apply is disabled for ENVIRONMENT=${ENVIRONMENT}; customer deployments must reconcile through Argo CD"
  fi
  assert_local_direct_apply_context
  require_cmd helm "Helm is required for the local direct-apply fallback."
  log "directly applying local runtime charts for workstation validation"
  kubectl apply -f "$ROOT/deploy/sandbox/base"
  ensure_platform_namespace ollama
  ensure_platform_namespace budget
  ensure_platform_namespace inference
  ensure_platform_namespace rag
  ensure_platform_namespace vector
  ensure_platform_namespace ai-agents
  kubectl label namespace ai-agents \
    platform.ai/traceable-sandbox=true \
    platform.ai/workload-kind=coding-agent \
    --overwrite >/dev/null
  kubectl apply -f "$ROOT/platform/model-catalog/k8s"
  helm upgrade --install budget-redis "$ROOT/deploy/charts/budget-redis" \
    --namespace budget \
    --set namespace.create=false
  helm upgrade --install ollama "$ROOT/deploy/charts/ollama" \
    --namespace ollama \
    --values "$ROOT/deploy/clusters/local/values/ollama.yaml" \
    --set namespace.create=false
  helm upgrade --install inference-gateway "$ROOT/deploy/charts/inference-gateway" \
    --namespace inference \
    --values "$ROOT/deploy/clusters/local/values/inference-gateway.yaml" \
    --set namespace.create=false \
    --set serviceMonitor.enabled=false \
    --set keda.enabled=false
  helm upgrade --install qdrant-vector-store "$ROOT/deploy/charts/qdrant-vector-store" \
    --namespace vector \
    --values "$ROOT/deploy/clusters/local/values/qdrant-vector-store.yaml" \
    --set namespace.create=false \
    --set serviceMonitor.enabled=false
  helm upgrade --install rag-service "$ROOT/deploy/charts/rag-service" \
    --namespace rag \
    --values "$ROOT/deploy/clusters/local/values/rag-service.yaml" \
    --set namespace.create=false \
    --set serviceMonitor.enabled=false \
    --set autoscaling.enabled=false
  helm upgrade --install agent-workspace "$ROOT/deploy/charts/agent-workspace" \
    --namespace ai-agents \
    --values "$ROOT/deploy/clusters/local/values/agent-workspace.yaml" \
    --set namespace.create=false
  kubectl -n inference rollout restart deploy/inference-gateway-inference-gateway >/dev/null
  kubectl -n vector rollout restart deploy/qdrant-vector-store >/dev/null
  kubectl -n rag rollout restart deploy/rag-service-rag-service >/dev/null
  kubectl -n budget rollout status deploy/budget-redis --timeout=5m
  kubectl -n ollama rollout status statefulset/ollama --timeout=5m
  kubectl -n vector rollout status deploy/qdrant-vector-store --timeout=5m
  kubectl -n inference rollout status deploy/inference-gateway-inference-gateway --timeout=5m
  kubectl -n rag rollout status deploy/rag-service-rag-service --timeout=5m
}

repository_unreachable() {
  grep -Eqi 'repository( |.* )(not found|unreachable|inaccessible)|authentication (required|failed)|failed to fetch|unable to access' <<<"$1"
}

fallback_or_fail_repository() {
  local details="$1"
  local root_app_file="deploy/gitops/argocd/root-app.yaml"
  if [[ "$ENVIRONMENT" == "customer" ]]; then
    root_app_file="deploy/gitops/argocd/root-app-customer.yaml and deploy/clusters/customer/apps.yaml"
    die "Argo CD cannot access the configured Git repository. Customer sync is fail-closed and made no local direct-apply fallback. Publish the repository or update ${root_app_file}. Details: ${details}"
  fi
  log "GitOps repository is not reachable from Argo CD; publish it or update ${root_app_file}. Using the context-guarded local direct-apply fallback."
  direct_apply_local
}

application_names() {
  printf '%s\n' "$ROOT_APP"
  awk '/^  name: / { print $2 }' "$ROOT/deploy/clusters/${ENVIRONMENT}/apps.yaml"
}

# Return 42 when repository access failed so local mode may use its guarded
# workstation fallback. All other non-zero returns are hard failures.
wait_for_argocd_applications() {
  local deadline=$(( SECONDS + ARGO_SYNC_TIMEOUT ))
  local name state sync health conditions pending
  local -a names=()
  mapfile -t names < <(application_names)
  log "waiting for ${#names[@]} Argo CD applications to become Synced and Healthy (timeout ${ARGO_SYNC_TIMEOUT}s)"

  while (( SECONDS < deadline )); do
    pending=0
    for name in "${names[@]}"; do
      if ! state="$(kubectl -n argocd get application "$name" -o jsonpath='{.status.sync.status}{"|"}{.status.health.status}{"|"}{.status.conditions[*].message}' 2>/dev/null)"; then
        pending=$(( pending + 1 ))
        continue
      fi
      IFS='|' read -r sync health conditions <<<"$state"
      if repository_unreachable "$conditions"; then
        log "Argo CD application ${name} reports an inaccessible repository: ${conditions}"
        return 42
      fi
      if [[ "$sync" != "Synced" || "$health" != "Healthy" ]]; then
        pending=$(( pending + 1 ))
      fi
    done
    if (( pending == 0 )); then
      log "all Argo CD applications are Synced and Healthy"
      return 0
    fi
    sleep "$ARGO_POLL_INTERVAL"
  done

  kubectl -n argocd get applications || true
  log "timed out with ${pending:-unknown} Argo CD applications not ready"
  return 1
}

if [[ "${LOCAL_DIRECT_APPLY:-0}" == "1" ]]; then
  direct_apply_local
  exit 0
fi

sync_output=""
sync_status=0
if has_cmd argocd; then
  # Core mode runs the Argo CD CLI directly against the Kubernetes API using the
  # active kubeconfig, so no argocd login or port-forward is needed.
  export ARGOCD_OPTS="${ARGOCD_OPTS:-} --core"
  sync_output="$(argocd app sync "$ROOT_APP" --timeout "$ARGO_SYNC_TIMEOUT" 2>&1)" || sync_status=$?
  printf '%s\n' "$sync_output"
  if repository_unreachable "$sync_output"; then
    fallback_or_fail_repository "$sync_output"
    exit 0
  fi
  if (( sync_status != 0 )); then
    die "argocd app sync failed with status ${sync_status}"
  fi
  argocd app wait "$ROOT_APP" --sync --timeout "$ARGO_SYNC_TIMEOUT"
else
  log "argocd CLI not found; requesting app refresh through kubectl annotations"
  kubectl -n argocd annotate application "$ROOT_APP" argocd.argoproj.io/refresh=hard --overwrite
fi

wait_status=0
wait_for_argocd_applications || wait_status=$?
if (( wait_status == 42 )); then
  root_conditions="$(kubectl -n argocd get application "$ROOT_APP" -o jsonpath='{.status.conditions[*].message}' 2>/dev/null || true)"
  fallback_or_fail_repository "${root_conditions:-repository unavailable}"
  exit 0
fi
if (( wait_status != 0 )); then
  die "Argo CD applications did not reach a healthy synchronized state"
fi

kubectl -n argocd get applications

# Workload rollouts give local smoke tests a direct readiness gate in addition
# to the Argo CD application health check. Customer mode is already gated on
# every declared Application above and does not assume local workload names.
if [[ "$ENVIRONMENT" == "local" ]]; then
  wait_for_rollout budget    deploy/budget-redis                         300
  wait_for_rollout ollama    statefulset/ollama                          600
  wait_for_rollout vector    deploy/qdrant-vector-store                  600
  wait_for_rollout inference deploy/inference-gateway-inference-gateway 600
  wait_for_rollout rag       deploy/rag-service-rag-service              600
fi
