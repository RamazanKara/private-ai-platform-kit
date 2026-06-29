#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"

CLUSTER_NAME="${CLUSTER_NAME:-private-ai-platform-kit}"
GATEWAY_IMAGE="${GATEWAY_IMAGE:-private-ai-platform-kit/inference-gateway:local}"
RAG_IMAGE="${RAG_IMAGE:-private-ai-platform-kit/rag-service:local}"
LOCAL_GATEWAY_HOST_PORT="${LOCAL_GATEWAY_HOST_PORT:-8080}"
DEFAULT_KIND_NODE_IMAGE="kindest/node:v1.35.1"
CGROUP_V1_KIND_NODE_IMAGE="kindest/node:v1.31.4"
LOCAL_KIND_NODE_IMAGE_WAS_SET="${LOCAL_KIND_NODE_IMAGE+x}"
LOCAL_KIND_NODE_IMAGE="${LOCAL_KIND_NODE_IMAGE:-$DEFAULT_KIND_NODE_IMAGE}"

if ! [[ "$LOCAL_GATEWAY_HOST_PORT" =~ ^[0-9]+$ ]] || (( LOCAL_GATEWAY_HOST_PORT < 1 || LOCAL_GATEWAY_HOST_PORT > 65535 )); then
  die "LOCAL_GATEWAY_HOST_PORT must be a TCP port between 1 and 65535"
fi

KIND_CONFIG="deploy/clusters/local/kind-config.yaml"
RENDERED_KIND_CONFIG=""

cleanup() {
  if [[ -n "$RENDERED_KIND_CONFIG" ]]; then
    rm -f "$RENDERED_KIND_CONFIG"
  fi
}
trap cleanup EXIT

require_cmd docker "Docker is required for kind."
require_cmd kind "Install kind to create the local cluster."
require_cmd kubectl "kubectl is required to inspect the local cluster."
require_cmd helm "Helm is required to install Argo CD locally."

cd "$ROOT"

DOCKER_CGROUP_VERSION="$(docker info --format '{{.CgroupVersion}}' 2>/dev/null || true)"
if [[ -z "$LOCAL_KIND_NODE_IMAGE_WAS_SET" && "$LOCAL_KIND_NODE_IMAGE" == "$DEFAULT_KIND_NODE_IMAGE" && "$DOCKER_CGROUP_VERSION" == "1" ]]; then
  LOCAL_KIND_NODE_IMAGE="$CGROUP_V1_KIND_NODE_IMAGE"
  log "Docker is using cgroup v1; using compatible kind node image ${LOCAL_KIND_NODE_IMAGE}"
fi

if ! kind get clusters | grep -qx "$CLUSTER_NAME"; then
  log "creating kind cluster ${CLUSTER_NAME} with gateway host port ${LOCAL_GATEWAY_HOST_PORT} and node image ${LOCAL_KIND_NODE_IMAGE}"
  if [[ "$LOCAL_GATEWAY_HOST_PORT" != "8080" || "$LOCAL_KIND_NODE_IMAGE" != "$DEFAULT_KIND_NODE_IMAGE" ]]; then
    RENDERED_KIND_CONFIG="$(mktemp)"
    sed \
      -e "s#image: kindest/node:.*#image: ${LOCAL_KIND_NODE_IMAGE}#" \
      -e "s/hostPort: 8080/hostPort: ${LOCAL_GATEWAY_HOST_PORT}/" \
      "$KIND_CONFIG" >"$RENDERED_KIND_CONFIG"
    kind create cluster --name "$CLUSTER_NAME" --config "$RENDERED_KIND_CONFIG"
  else
    kind create cluster --name "$CLUSTER_NAME" --config "$KIND_CONFIG"
  fi
else
  log "kind cluster ${CLUSTER_NAME} already exists"
fi

log "building gateway image ${GATEWAY_IMAGE}"
docker build -t "$GATEWAY_IMAGE" src/inference-gateway
kind load docker-image "$GATEWAY_IMAGE" --name "$CLUSTER_NAME"

log "building RAG service image ${RAG_IMAGE}"
docker build -t "$RAG_IMAGE" src/rag-service
kind load docker-image "$RAG_IMAGE" --name "$CLUSTER_NAME"

log "ensuring Argo CD namespace exists"
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
log "local cluster is ready; run make bootstrap-argocd next"
