#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"

CLUSTER_NAME="${CLUSTER_NAME:-ai-platform-ops-lab}"
GATEWAY_IMAGE="${GATEWAY_IMAGE:-ai-platform-ops-lab/inference-gateway:local}"
RAG_IMAGE="${RAG_IMAGE:-ai-platform-ops-lab/rag-service:local}"

require_cmd docker "Docker is required for kind."
require_cmd kind "Install kind to create the local cluster."
require_cmd kubectl "kubectl is required to inspect the local cluster."
require_cmd helm "Helm is required to install Argo CD locally."

cd "$ROOT"

if ! kind get clusters | grep -qx "$CLUSTER_NAME"; then
  log "creating kind cluster ${CLUSTER_NAME}"
  kind create cluster --name "$CLUSTER_NAME" --config clusters/local/kind-config.yaml
else
  log "kind cluster ${CLUSTER_NAME} already exists"
fi

log "building gateway image ${GATEWAY_IMAGE}"
docker build -t "$GATEWAY_IMAGE" services/inference-gateway
kind load docker-image "$GATEWAY_IMAGE" --name "$CLUSTER_NAME"

log "building RAG service image ${RAG_IMAGE}"
docker build -t "$RAG_IMAGE" services/rag-service
kind load docker-image "$RAG_IMAGE" --name "$CLUSTER_NAME"

log "ensuring Argo CD namespace exists"
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
log "local cluster is ready; run make bootstrap-argocd next"
