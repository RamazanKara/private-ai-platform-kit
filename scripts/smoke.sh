#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
require_cmd kubectl "kubectl is required for smoke testing."

NAMESPACE="${NAMESPACE:-inference}"
SERVICE="${SERVICE:-inference-gateway-inference-gateway}"
LOCAL_PORT="${LOCAL_PORT:-18080}"
SERVICE_PORT="${SERVICE_PORT:-8080}"
RUNTIME_BACKEND="${RUNTIME_BACKEND:-ollama}"
SANDBOX_ID="${SANDBOX_ID:-local-lab}"
REQUEST_ID="${REQUEST_ID:-smoke-$(date -u +%Y%m%dT%H%M%SZ)}"
TRACEPARENT="${TRACEPARENT:-00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01}"
PLATFORM_API_KEY="${PLATFORM_API_KEY:-local-development-only}"
HEALTH_HEADERS=""
CHAT_HEADERS=""

cleanup() {
  kill "$PF_PID" >/dev/null 2>&1 || true
  rm -f "$HEALTH_HEADERS" "$CHAT_HEADERS"
}

log "port-forwarding ${NAMESPACE}/${SERVICE}"
kubectl -n "$NAMESPACE" port-forward --address 127.0.0.1 "svc/${SERVICE}" "${LOCAL_PORT}:${SERVICE_PORT}" >/tmp/ai-platform-ops-lab-port-forward.log 2>&1 &
PF_PID=$!
trap cleanup EXIT
sleep 3

HEALTH_HEADERS="$(mktemp)"
CHAT_HEADERS="$(mktemp)"

curl -fsS -D "$HEALTH_HEADERS" \
  -H "X-Request-ID: ${REQUEST_ID}-health" \
  -H "X-Sandbox-ID: ${SANDBOX_ID}" \
  -H "traceparent: ${TRACEPARENT}" \
  "http://127.0.0.1:${LOCAL_PORT}/healthz"
printf '\n'
grep -qi "^x-sandbox-id: ${SANDBOX_ID}" "$HEALTH_HEADERS"

curl -fsS -D "$CHAT_HEADERS" "http://127.0.0.1:${LOCAL_PORT}/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -H "X-Request-ID: ${REQUEST_ID}-chat" \
  -H "X-Sandbox-ID: ${SANDBOX_ID}" \
  -H "X-API-Key: ${PLATFORM_API_KEY}" \
  -H "traceparent: ${TRACEPARENT}" \
  -d '{"messages":[{"role":"user","content":"Say hello from AI Platform Ops Lab"}]}' \
  | python3 -m json.tool
grep -qi "^x-request-id: ${REQUEST_ID}-chat" "$CHAT_HEADERS"
grep -qi "^x-sandbox-id: ${SANDBOX_ID}" "$CHAT_HEADERS"

log "smoke test completed for ${RUNTIME_BACKEND}"
