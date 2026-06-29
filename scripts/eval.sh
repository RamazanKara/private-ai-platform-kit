#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
cd "$ROOT"

require_cmd python3 "Python 3 is required for eval execution."

if [[ ! -x src/inference-gateway/.venv/bin/python ]]; then
  log "creating inference gateway Python environment for evals"
  ./scripts/test-gateway.sh >/dev/null
fi

SUITE="${SUITE:-evals/smoke-suite.yaml}"
GATEWAY_URL="${GATEWAY_URL:-}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
output_json="results/evals/eval-${timestamp}.json"
output_md="results/evals/eval-${timestamp}.md"
PLATFORM_API_KEY="${PLATFORM_API_KEY:-local-development-only}"

if [[ -z "$GATEWAY_URL" ]]; then
  require_cmd kubectl "kubectl is required when GATEWAY_URL is not set."
  local_port="${GATEWAY_PORT:-18082}"
  log "port-forwarding inference gateway on 127.0.0.1:${local_port}"
  kubectl -n inference port-forward --address 127.0.0.1 svc/inference-gateway-inference-gateway "${local_port}:8080" >/tmp/private-ai-platform-kit-eval-port-forward.log 2>&1 &
  pf_pid="$!"
  trap 'kill "$pf_pid" >/dev/null 2>&1 || true' EXIT
  sleep 2
  GATEWAY_URL="http://127.0.0.1:${local_port}"
fi

log "running eval suite ${SUITE} against ${GATEWAY_URL}"
src/inference-gateway/.venv/bin/python scripts/eval-suite.py \
  --suite "$SUITE" \
  --gateway-url "$GATEWAY_URL" \
  --output-json "$output_json" \
  --output-md "$output_md" \
  --api-key "$PLATFORM_API_KEY"
log "eval evidence written to ${output_json} and ${output_md}"
