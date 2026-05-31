#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"

require_cmd helm "Helm is required to install the agent workspace chart."
require_cmd kubectl "kubectl is required to provision agent workspaces."

ENVIRONMENT="${ENVIRONMENT:-local}"
VALUES_FILE="$ROOT/clusters/${ENVIRONMENT}/values/agent-workspace.yaml"
NAMESPACE="${AGENT_NAMESPACE:-ai-agents}"

if [[ ! -f "$VALUES_FILE" ]]; then
  die "missing agent workspace values file ${VALUES_FILE}"
fi

log "installing agent workspace into ${NAMESPACE} using ${ENVIRONMENT} values"
helm upgrade --install agent-workspace "$ROOT/charts/agent-workspace" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --values "$VALUES_FILE"

kubectl -n "$NAMESPACE" get configmap agent-platform-contract >/dev/null
log "agent workspace ${NAMESPACE} is ready"
