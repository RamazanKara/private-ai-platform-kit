#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
require_cmd k6 "Install k6 to run load tests."

cd "$ROOT"
./scripts/bootstrap-python.sh

choose_port() {
  python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

wait_http() {
  local url="$1"
  python3 - "$url" <<'PY'
import sys
import time
from urllib.request import urlopen

url = sys.argv[1]
deadline = time.time() + 20
last_error = ""
while time.time() < deadline:
    try:
        with urlopen(url, timeout=1) as response:
            if response.status < 500:
                raise SystemExit(0)
    except Exception as exc:
        last_error = str(exc)
        time.sleep(0.25)
raise SystemExit(f"timed out waiting for {url}: {last_error}")
PY
}

MOCK_RUNTIME_PORT="${MOCK_RUNTIME_PORT:-$(choose_port)}"
GATEWAY_PORT="${GATEWAY_PORT:-$(choose_port)}"
LOADTEST_MODEL="${MODEL_ID:-qwen3:0.6b}"
LOADTEST_DURATION="${LOADTEST_DURATION:-15s}"
LOADTEST_VUS="${LOADTEST_VUS:-2}"
PLATFORM_API_KEY="${PLATFORM_API_KEY:-local-development-only}"
LOCAL_API_KEY_SHA256="ed20191044553dac8f9c45e62062dd18e7dc1f898a897240b4179fb84fea3db4"
LOG_DIR="${LOG_DIR:-/tmp/private-ai-platform-kit-loadtest}"
mkdir -p "$LOG_DIR" results/loadtest

cleanup() {
  local status=$?
  if [[ -n "${GATEWAY_PID:-}" ]]; then
    kill "$GATEWAY_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "${MOCK_RUNTIME_PID:-}" ]]; then
    kill "$MOCK_RUNTIME_PID" >/dev/null 2>&1 || true
  fi
  wait "${GATEWAY_PID:-}" >/dev/null 2>&1 || true
  wait "${MOCK_RUNTIME_PID:-}" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT INT TERM

log "starting local mock runtime on 127.0.0.1:${MOCK_RUNTIME_PORT}"
python3 tests/load/mock-runtime.py --port "$MOCK_RUNTIME_PORT" >"$LOG_DIR/mock-runtime.log" 2>&1 &
MOCK_RUNTIME_PID=$!
wait_http "http://127.0.0.1:${MOCK_RUNTIME_PORT}/healthz"

log "starting inference gateway on 127.0.0.1:${GATEWAY_PORT}"
(
  cd services/inference-gateway
  PYTHONPATH="$PWD" \
    RUNTIME_BACKEND=ollama \
    OLLAMA_BASE_URL="http://127.0.0.1:${MOCK_RUNTIME_PORT}" \
    MODEL_ID="$LOADTEST_MODEL" \
    ALLOWED_MODELS="$LOADTEST_MODEL" \
    API_KEY_AUTH_ENABLED=true \
    API_KEY_SHA256S="$LOCAL_API_KEY_SHA256" \
    REQUEST_TIMEOUT_SECONDS=5 \
    AUDIT_LOG_ENABLED=false \
    .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$GATEWAY_PORT"
) >"$LOG_DIR/inference-gateway.log" 2>&1 &
GATEWAY_PID=$!
wait_http "http://127.0.0.1:${GATEWAY_PORT}/healthz"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="results/loadtest/loadtest-${STAMP}.json"
SUMMARY="results/loadtest/summary-${STAMP}.md"

log "running local gateway load test for ${LOADTEST_DURATION} with ${LOADTEST_VUS} VUs"
GATEWAY_URL="http://127.0.0.1:${GATEWAY_PORT}" \
MODEL_ID="$LOADTEST_MODEL" \
RUNTIME_BACKEND=local-mock \
DURATION="$LOADTEST_DURATION" \
VUS="$LOADTEST_VUS" \
PLATFORM_API_KEY="$PLATFORM_API_KEY" \
  k6 run --summary-export "$OUT" tests/load/chat-completions.js
python3 tests/load/summarize.py "$OUT" "$SUMMARY"
log "wrote ${OUT} and ${SUMMARY}"
