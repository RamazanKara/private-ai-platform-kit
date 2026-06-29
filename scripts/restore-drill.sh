#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
cd "$ROOT"

RUNTIME="${RUNTIME:-local}"
mkdir -p .out/results/restore-drill
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

# -----------------------------------------------------------------------------
# Real data-recovery drill for Qdrant (opt-in, live local cluster only).
#
# Unlike the Redis path below, this exercises an end-to-end recovery of real
# stored vectors: seed a known set of points into a throwaway test collection,
# export a Qdrant snapshot, delete the collection, restore it from the snapshot,
# and assert the point count is non-trivially recovered. It proves that vector
# data is actually recoverable -- not just that the restore tooling runs.
#
# Guarded so it never runs by accident: requires RUNTIME=local AND the explicit
# RESTORE_DRILL_QDRANT_DATA=1 flag, and a reachable Qdrant. It operates only on
# its own ephemeral test collection so it never touches production knowledge.
# -----------------------------------------------------------------------------
qdrant_data_restore_drill() {
  require_cmd curl "curl is required for the Qdrant data-restore drill."
  require_cmd python3 "python3 is required for the Qdrant data-restore drill."

  local base_url="${RESTORE_DRILL_QDRANT_URL:-http://127.0.0.1:6333}"
  local collection="${RESTORE_DRILL_QDRANT_COLLECTION:-restore-drill-data-probe}"
  local dimensions="${RESTORE_DRILL_QDRANT_DIMENSIONS:-4}"
  local point_count="${RESTORE_DRILL_QDRANT_POINTS:-16}"
  local report=".out/results/restore-drill/qdrant-data-restore-${STAMP}.json"

  log "REAL DATA DRILL: Qdrant vector data-restore against ${base_url} collection '${collection}'"
  log "this drill seeds, snapshots, deletes, and restores real vectors and asserts recovery"

  RESTORE_DRILL_QDRANT_URL="$base_url" \
  RESTORE_DRILL_QDRANT_COLLECTION="$collection" \
  RESTORE_DRILL_QDRANT_DIMENSIONS="$dimensions" \
  RESTORE_DRILL_QDRANT_POINTS="$point_count" \
  RESTORE_DRILL_QDRANT_REPORT="$report" \
  python3 - <<'PY' | tee "$report"
import json
import os
import sys
import urllib.error
import urllib.request

base = os.environ["RESTORE_DRILL_QDRANT_URL"].rstrip("/")
collection = os.environ["RESTORE_DRILL_QDRANT_COLLECTION"]
dims = int(os.environ["RESTORE_DRILL_QDRANT_DIMENSIONS"])
count = int(os.environ["RESTORE_DRILL_QDRANT_POINTS"])


def request(method, path, payload=None):
    url = f"{base}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode()
    return json.loads(body) if body else {}


def point_total():
    result = request("POST", f"/collections/{collection}/points/count", {"exact": True})
    return int(result.get("result", {}).get("count", -1))


def fail(message):
    print(json.dumps([{
        "name": "qdrant-data-restore",
        "provider": "qdrant",
        "type": "real-data-recovery",
        "status": "fail",
        "validation_passed": False,
        "summary": message,
    }], indent=2))
    sys.exit(1)


try:
    # Clean slate: drop any leftover probe collection from a prior run.
    try:
        request("DELETE", f"/collections/{collection}")
    except urllib.error.HTTPError:
        pass

    # 1. Seed a known set of real vectors.
    request("PUT", f"/collections/{collection}", {
        "vectors": {"size": dims, "distance": "Cosine"},
    })
    points = [
        {
            "id": i,
            "vector": [((i + j) % 7) / 7.0 for j in range(dims)],
            "payload": {"probe": f"point-{i}"},
        }
        for i in range(count)
    ]
    request("PUT", f"/collections/{collection}/points?wait=true", {"points": points})
    seeded = point_total()
    if seeded != count:
        fail(f"seed mismatch: expected {count} points, found {seeded}")

    # 2. Snapshot/export the collection.
    snapshot = request("POST", f"/collections/{collection}/snapshots?wait=true")
    snapshot_name = snapshot.get("result", {}).get("name")
    if not snapshot_name:
        fail("snapshot did not return a name")

    # 3. Destroy the live collection (simulate data loss).
    request("DELETE", f"/collections/{collection}")

    # 4. Restore the collection from the snapshot.
    request("PUT", f"/collections/{collection}/snapshots/recover", {
        "location": f"file:///qdrant/snapshots/{collection}/{snapshot_name}",
    })

    # 5. Assert the vectors are non-trivially recovered.
    recovered = point_total()
    ok = recovered == count and recovered > 0
    result = [{
        "name": "qdrant-data-restore",
        "provider": "qdrant",
        "type": "real-data-recovery",
        "status": "pass" if ok else "fail",
        "validation_passed": ok,
        "seeded_points": seeded,
        "recovered_points": recovered,
        "snapshot": snapshot_name,
        "checks": [{
            "name": "recovered-point-count",
            "type": "key_count",
            "expected": f"== {count}",
            "actual": str(recovered),
            "passed": ok,
        }],
    }]
    print(json.dumps(result, indent=2))
    sys.exit(0 if ok else 1)
except (urllib.error.URLError, urllib.error.HTTPError) as exc:
    fail(f"Qdrant unreachable or rejected request: {exc}")
PY
  log "REAL DATA DRILL complete; report at ${report}"
}

if [[ "$RUNTIME" == "local" && "${RESTORE_DRILL_QDRANT_DATA:-0}" == "1" ]]; then
  qdrant_data_restore_drill
  exit 0
fi

if [[ "$RUNTIME" == "local" ]]; then
  # FIXTURE SMOKE ONLY: this validates the restore *pipeline/tooling* using a
  # synthetic 2-key Redis AOF fixture. It proves restore-drill can stand up a
  # disposable target, replay an AOF, and run data checks -- it does NOT prove
  # that any production data store is recoverable. For a real data-recovery
  # drill, see qdrant_data_restore_drill above (RESTORE_DRILL_QDRANT_DATA=1) or
  # chaos/drills/qdrant-data-restore.yaml.
  require_cmd restore-drill "Install with: go install github.com/RamazanKara/restore-drill/cmd/restore-drill@v1.0.1"
  log "restore-tooling smoke: validating the restore pipeline against a synthetic Redis AOF fixture (not production data)"
  export RESTORE_DRILL_REDIS_AOF="${RESTORE_DRILL_REDIS_AOF:-/tmp/private-ai-platform-kit-redis.aof}"
  python3 - <<'PY'
import os
from pathlib import Path

target = Path(os.environ["RESTORE_DRILL_REDIS_AOF"])
# Synthetic fixture: two health-check keys used only to exercise the restore
# tooling end to end. This is not a backup of real lab state.
target.write_bytes(
    b"*2\r\n$6\r\nSELECT\r\n$1\r\n0\r\n"
    b"*3\r\n$3\r\nSET\r\n$20\r\nsession:health-check\r\n$2\r\nok\r\n"
    b"*3\r\n$3\r\nSET\r\n$9\r\ncache:hot\r\n$4\r\nwarm\r\n"
)
PY
  restore-drill run \
    --config deploy/backup/restore-drill/drills/local-redis-aof.yaml \
    --runtime docker \
    --format json | tee ".out/results/restore-drill/restore-drill-${STAMP}.json"
  log "restore-tooling smoke passed: the restore pipeline works (fixture validation only, not a production-data recovery proof)"
else
  require_cmd kubectl "kubectl is required for Kubernetes restore-drill deployment."
  kubectl apply -f deploy/backup/restore-drill/k8s/
fi

if [[ "${1:-}" == "--include-velero" ]]; then
  require_cmd kubectl "kubectl is required for the Velero namespace drill."
  kubectl apply -f deploy/backup/velero/velero-smoke-namespace.yaml
  log "Velero smoke namespace applied; run the Velero Backup and Restore resources after Velero is installed."
fi
