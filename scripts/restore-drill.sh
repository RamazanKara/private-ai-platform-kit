#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
cd "$ROOT"

RUNTIME="${RUNTIME:-local}"
mkdir -p results/restore-drill
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

if [[ "$RUNTIME" == "local" ]]; then
  require_cmd restore-drill "Install with: go install github.com/RamazanKara/restore-drill/cmd/restore-drill@v1.0.1"
  export RESTORE_DRILL_REDIS_AOF="${RESTORE_DRILL_REDIS_AOF:-/tmp/private-ai-platform-kit-redis.aof}"
  python3 - <<'PY'
import os
from pathlib import Path

target = Path(os.environ["RESTORE_DRILL_REDIS_AOF"])
target.write_bytes(
    b"*2\r\n$6\r\nSELECT\r\n$1\r\n0\r\n"
    b"*3\r\n$3\r\nSET\r\n$20\r\nsession:health-check\r\n$2\r\nok\r\n"
    b"*3\r\n$3\r\nSET\r\n$9\r\ncache:hot\r\n$4\r\nwarm\r\n"
)
PY
  restore-drill run \
    --config backup/restore-drill/drills/local-redis-aof.yaml \
    --runtime docker \
    --format json | tee "results/restore-drill/restore-drill-${STAMP}.json"
else
  require_cmd kubectl "kubectl is required for Kubernetes restore-drill deployment."
  kubectl apply -f backup/restore-drill/k8s/
fi

if [[ "${1:-}" == "--include-velero" ]]; then
  require_cmd kubectl "kubectl is required for the Velero namespace drill."
  kubectl apply -f backup/velero/velero-smoke-namespace.yaml
  log "Velero smoke namespace applied; run the Velero Backup and Restore resources after Velero is installed."
fi
