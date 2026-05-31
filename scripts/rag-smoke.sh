#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
require_cmd kubectl "kubectl is required for RAG smoke testing."
require_cmd python3 "python3 is required to inspect RAG smoke responses."

NAMESPACE="${NAMESPACE:-rag}"
SERVICE="${SERVICE:-rag-service-rag-service}"
LOCAL_PORT="${LOCAL_PORT:-18083}"
SERVICE_PORT="${SERVICE_PORT:-8080}"
SANDBOX_ID="${SANDBOX_ID:-agent-lab}"
REQUEST_ID="${REQUEST_ID:-rag-smoke-$(date -u +%Y%m%dT%H%M%SZ)}"
TRACEPARENT="${TRACEPARENT:-00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01}"
PLATFORM_API_KEY="${PLATFORM_API_KEY:-local-development-only}"
EXPECTED_RAG_BACKEND="${EXPECTED_RAG_BACKEND:-}"
HEALTH_HEADERS=""
HEALTH_BODY=""
QUERY_HEADERS=""
QUERY_BODY=""

cleanup() {
  kill "$PF_PID" >/dev/null 2>&1 || true
  rm -f "$HEALTH_HEADERS" "$HEALTH_BODY" "$QUERY_HEADERS" "$QUERY_BODY"
}

log "port-forwarding ${NAMESPACE}/${SERVICE}"
kubectl -n "$NAMESPACE" port-forward --address 127.0.0.1 "svc/${SERVICE}" "${LOCAL_PORT}:${SERVICE_PORT}" >/tmp/private-ai-platform-kit-rag-port-forward.log 2>&1 &
PF_PID=$!
trap cleanup EXIT
sleep 3

HEALTH_HEADERS="$(mktemp)"
HEALTH_BODY="$(mktemp)"
QUERY_HEADERS="$(mktemp)"
QUERY_BODY="$(mktemp)"

curl -fsS -D "$HEALTH_HEADERS" \
  -o "$HEALTH_BODY" \
  -H "X-Request-ID: ${REQUEST_ID}-health" \
  -H "X-Sandbox-ID: ${SANDBOX_ID}" \
  -H "traceparent: ${TRACEPARENT}" \
  "http://127.0.0.1:${LOCAL_PORT}/healthz"
python3 -m json.tool "$HEALTH_BODY"
printf '\n'
grep -qi "^x-sandbox-id: ${SANDBOX_ID}" "$HEALTH_HEADERS"
if [[ -n "$EXPECTED_RAG_BACKEND" ]]; then
  python3 - "$HEALTH_BODY" "$EXPECTED_RAG_BACKEND" <<'PY'
import json
import sys

body = json.load(open(sys.argv[1], encoding="utf-8"))
expected = sys.argv[2]
actual = body.get("retrieval_backend")
assert actual == expected, f"expected RAG backend {expected}, got {actual}"
PY
fi

curl -fsS -D "$QUERY_HEADERS" \
  -o "$QUERY_BODY" \
  "http://127.0.0.1:${LOCAL_PORT}/v1/rag/query" \
  -H 'Content-Type: application/json' \
  -H "X-Request-ID: ${REQUEST_ID}-query" \
  -H "X-Sandbox-ID: ${SANDBOX_ID}" \
  -H "X-API-Key: ${PLATFORM_API_KEY}" \
  -H "traceparent: ${TRACEPARENT}" \
  -d '{"query":"How should coding agents use the inference gateway and trace headers?","top_k":2}'

python3 -m json.tool "$QUERY_BODY"
python3 - "$QUERY_BODY" <<'PY'
import json
import sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["sandbox_id"]
assert body["results"], "expected at least one RAG result"
assert body["grounded_messages"], "expected grounded OpenAI-compatible messages"
assert "query_sha256" in body
PY
grep -qi "^x-request-id: ${REQUEST_ID}-query" "$QUERY_HEADERS"
grep -qi "^x-sandbox-id: ${SANDBOX_ID}" "$QUERY_HEADERS"

log "RAG smoke completed for ${SANDBOX_ID}"
