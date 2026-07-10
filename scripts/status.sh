#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
require_cmd kubectl "kubectl is required for platform status."

context="$(kubectl config current-context 2>/dev/null || true)"
[[ -n "$context" ]] || die "no active Kubernetes context"
if ! kubectl cluster-info >/dev/null 2>&1; then
  die "Kubernetes context ${context} is not reachable"
fi

log "context: ${context}"
log "nodes"
kubectl get nodes -o wide

if kubectl -n argocd get applications.argoproj.io >/dev/null 2>&1; then
  log "Argo CD applications"
  kubectl -n argocd get applications.argoproj.io \
    -o custom-columns='NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status,REVISION:.status.sync.revision'
fi

log "platform workloads"
kubectl get deployments,statefulsets -A -l app.kubernetes.io/part-of=private-ai-platform-kit

not_ready="$(kubectl get pods -A -l app.kubernetes.io/part-of=private-ai-platform-kit \
  -o custom-columns='NAMESPACE:.metadata.namespace,NAME:.metadata.name,PHASE:.status.phase,READY:.status.containerStatuses[*].ready' \
  --no-headers 2>/dev/null | awk '$3 != "Succeeded" && ($3 != "Running" || $4 ~ /false/ || $4 == "<none>") {print $1 "/" $2}' || true)"
if [[ -n "$not_ready" ]]; then
  log "pods requiring attention"
  printf '%s\n' "$not_ready"
  log "recent warning events"
  kubectl get events -A --field-selector type=Warning --sort-by=.lastTimestamp | tail -30 || true
  exit 1
fi

log "all discovered platform pods are ready"
