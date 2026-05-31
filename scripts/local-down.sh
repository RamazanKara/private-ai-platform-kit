#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"

CLUSTER_NAME="${CLUSTER_NAME:-ai-platform-ops-lab}"
require_cmd kind "kind is required to remove the local cluster."

if kind get clusters | grep -qx "$CLUSTER_NAME"; then
  log "deleting kind cluster ${CLUSTER_NAME}"
  kind delete cluster --name "$CLUSTER_NAME"
else
  log "kind cluster ${CLUSTER_NAME} does not exist"
fi

