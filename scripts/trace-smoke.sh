#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"

require_cmd kubectl "kubectl is required to run the sandbox smoke test."

cd "$ROOT"

log "applying traceable sandbox base controls"
kubectl apply -f deploy/sandbox/base

log "recreating sandbox trace smoke job"
kubectl -n ai-sandbox delete job ai-sandbox-trace-smoke --ignore-not-found
kubectl apply -f deploy/sandbox/tests/trace-smoke-job.yaml

log "waiting for sandbox trace smoke job"
if ! kubectl -n ai-sandbox wait --for=condition=complete job/ai-sandbox-trace-smoke --timeout=120s; then
  kubectl -n ai-sandbox logs job/ai-sandbox-trace-smoke || true
  exit 1
fi

kubectl -n ai-sandbox logs job/ai-sandbox-trace-smoke
log "sandbox trace smoke completed"
