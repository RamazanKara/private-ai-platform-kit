#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"

require_cmd kubectl "kubectl is required to install the agent-sandbox controller."

VERSION="${AGENT_SANDBOX_VERSION:-v0.5.0}"
VENDOR_DIR="$ROOT/deploy/vendor/agent-sandbox/${VERSION}"

if [[ ! -d "$VENDOR_DIR" ]]; then
  die "missing vendored agent-sandbox manifests at ${VENDOR_DIR}"
fi

# Server-side apply: the vendored CRDs exceed the client-side
# last-applied-configuration annotation limit.
log "installing agent-sandbox ${VERSION} from vendored manifests"
kubectl apply --server-side -f "$VENDOR_DIR/manifest.yaml"
kubectl apply --server-side -f "$VENDOR_DIR/extensions.yaml"

log "waiting for agent-sandbox controllers to become available"
kubectl -n agent-sandbox-system wait --for=condition=Available deployment --all --timeout=180s

log "agent-sandbox ${VERSION} is ready"
