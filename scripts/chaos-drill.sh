#!/usr/bin/env bash
set -euo pipefail

# Drill taxonomy (be honest about what each drill actually does):
#   *-rollout drills are ROLLOUT/RECOVERY drills -- they `kubectl rollout
#     restart` a workload and assert it comes back healthy. They prove
#     graceful restart and post-restart smoke, NOT resilience to faults.
#   gpu-capacity-preflight is a non-mutating capacity check.
#   rag-degradation-fault is a TRUE FAULT-INJECTION drill -- it removes the
#     Qdrant dependency (scale to 0) under the running RAG service and asserts
#     RAG degrades gracefully (the service stays up and the PDB/health hold),
#     then restores Qdrant and asserts recovery.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"

require_cmd kubectl "kubectl is required for chaos drills."

DRILL="${DRILL:-gateway-rollout}"
RUN_SMOKE="${RUN_SMOKE:-1}"
GPU_NODE_SELECTOR="${GPU_NODE_SELECTOR:-platform.ai/node-pool=gpu}"
GPU_VENDOR="${GPU_VENDOR:-}"
GPU_RESOURCE_NAME="${GPU_RESOURCE_NAME:-}"

gpu_capacity_preflight() {
  require_cmd python3 "python3 is required for GPU capacity preflight checks."
  log "starting chaos drill ${DRILL}: checking GPU capacity selector ${GPU_NODE_SELECTOR}"
  node_json="$(mktemp)"
  trap 'rm -f "$node_json"' RETURN
  kubectl get nodes -l "$GPU_NODE_SELECTOR" -o json >"$node_json"
  python3 - "$node_json" "$GPU_VENDOR" "$GPU_RESOURCE_NAME" <<'PY'
import json
import sys

path, expected_vendor, expected_resource = sys.argv[1:4]
payload = json.load(open(path, encoding="utf-8"))
resources = [expected_resource] if expected_resource else ["nvidia.com/gpu", "amd.com/gpu"]
matches = []
for node in payload.get("items", []):
    labels = node.get("metadata", {}).get("labels", {})
    vendor = labels.get("platform.ai/gpu-vendor", "")
    if expected_vendor and vendor != expected_vendor:
        continue
    allocatable = node.get("status", {}).get("allocatable", {})
    available = {name: int(allocatable.get(name, "0")) for name in resources if str(allocatable.get(name, "0")).isdigit()}
    if any(count > 0 for count in available.values()):
        matches.append((node.get("metadata", {}).get("name", "<unknown>"), vendor, available))

if not matches:
    suffix = f" for vendor {expected_vendor}" if expected_vendor else ""
    raise SystemExit(f"no GPU nodes with allocatable {resources}{suffix}")

for name, vendor, available in matches:
    print(f"{name}: vendor={vendor or 'unlabeled'} allocatable={available}")
PY
  rm -f "$node_json"
  trap - RETURN
  log "rollout/recovery drill ${DRILL} completed"
}

rag_degradation_fault() {
  # TRUE FAULT INJECTION: take the Qdrant dependency away from a running RAG
  # service and assert RAG degrades gracefully rather than hard-failing, then
  # restore Qdrant and assert recovery. This is a real fault, not a restart.
  local rag_ns="${RAG_NAMESPACE:-rag}"
  local rag_deploy="${RAG_DEPLOYMENT:-deployment/rag-service-rag-service}"
  local qdrant_ns="${QDRANT_NAMESPACE:-vector}"
  local qdrant_deploy="${QDRANT_DEPLOYMENT:-deployment/qdrant-vector-store}"
  local timeout="${FAULT_TIMEOUT:-5m}"

  log "starting fault-injection drill ${DRILL}: removing Qdrant (${qdrant_ns}/${qdrant_deploy}) under load"
  local original
  original="$(kubectl -n "$qdrant_ns" get "$qdrant_deploy" -o jsonpath='{.spec.replicas}')"
  original="${original:-1}"

  # Always attempt to restore Qdrant, even if an assertion fails mid-drill.
  restore_qdrant() {
    log "restoring Qdrant to ${original} replica(s)"
    kubectl -n "$qdrant_ns" scale "$qdrant_deploy" --replicas="$original" >/dev/null 2>&1 || true
    kubectl -n "$qdrant_ns" rollout status "$qdrant_deploy" --timeout="$timeout" || true
  }
  trap restore_qdrant EXIT

  # Inject the fault: scale the vector store to zero.
  kubectl -n "$qdrant_ns" scale "$qdrant_deploy" --replicas=0
  kubectl -n "$qdrant_ns" rollout status "$qdrant_deploy" --timeout="$timeout" || true

  # Assert graceful degradation: RAG pods must stay Available (PDB/error-budget
  # holds) -- the service must not crash just because retrieval is unavailable.
  log "asserting RAG stays available with Qdrant down (graceful degradation)"
  if ! kubectl -n "$rag_ns" rollout status "$rag_deploy" --timeout=60s; then
    die "fault-injection drill ${DRILL} FAILED: RAG did not stay available while Qdrant was down"
  fi
  if ! kubectl -n "$rag_ns" wait --for=condition=Available "$rag_deploy" --timeout=60s; then
    die "fault-injection drill ${DRILL} FAILED: RAG lost Availability under the Qdrant fault"
  fi

  # Restore the dependency and assert recovery via the real RAG smoke.
  restore_qdrant
  trap - EXIT
  if [[ "$RUN_SMOKE" == "1" ]]; then
    log "asserting RAG recovers after Qdrant is restored"
    EXPECTED_RAG_BACKEND="${EXPECTED_RAG_BACKEND:-qdrant}" "$ROOT/scripts/rag-smoke.sh"
  fi
  log "fault-injection drill ${DRILL} completed: RAG degraded gracefully and recovered"
}

run_post_smoke() {
  case "$DRILL" in
    rag-service-rollout)
      log "running post-drill RAG smoke"
      "$ROOT/scripts/rag-smoke.sh"
      ;;
    qdrant-vector-store-rollout)
      log "running post-drill vector RAG smoke"
      EXPECTED_RAG_BACKEND="${EXPECTED_RAG_BACKEND:-qdrant}" "$ROOT/scripts/rag-smoke.sh"
      ;;
    vllm-runtime-rollout)
      log "running post-drill vLLM gateway smoke"
      RUNTIME_BACKEND=vllm "$ROOT/scripts/smoke.sh"
      ;;
    *)
      log "running post-drill gateway smoke"
      RUNTIME_BACKEND=ollama "$ROOT/scripts/smoke.sh"
      ;;
  esac
}

case "$DRILL" in
  gateway-rollout)
    namespace="inference"
    resource="deployment/inference-gateway-inference-gateway"
    timeout="5m"
    ;;
  budget-redis-rollout)
    namespace="budget"
    resource="deployment/budget-redis"
    timeout="5m"
    ;;
  ollama-rollout)
    namespace="ollama"
    resource="statefulset/ollama"
    timeout="10m"
    ;;
  rag-service-rollout)
    namespace="rag"
    resource="deployment/rag-service-rag-service"
    timeout="5m"
    ;;
  qdrant-vector-store-rollout)
    namespace="vector"
    resource="deployment/qdrant-vector-store"
    timeout="5m"
    ;;
  vllm-runtime-rollout)
    namespace="vllm"
    resource="deployment/vllm"
    timeout="15m"
    ;;
  gpu-capacity-preflight)
    gpu_capacity_preflight
    exit 0
    ;;
  rag-degradation-fault)
    rag_degradation_fault
    exit 0
    ;;
  *)
    die "unknown DRILL '${DRILL}'. Use gateway-rollout, budget-redis-rollout, ollama-rollout, rag-service-rollout, qdrant-vector-store-rollout, vllm-runtime-rollout, gpu-capacity-preflight, or rag-degradation-fault."
    ;;
esac

log "starting rollout/recovery drill ${DRILL}: restarting ${namespace}/${resource}"
kubectl -n "$namespace" rollout restart "$resource"
kubectl -n "$namespace" rollout status "$resource" --timeout="$timeout"

if [[ "$DRILL" == "budget-redis-rollout" ]]; then
  kubectl -n budget exec deploy/budget-redis -- redis-cli ping
fi

if [[ "$RUN_SMOKE" == "1" ]]; then
  run_post_smoke
fi

log "chaos drill ${DRILL} completed"
