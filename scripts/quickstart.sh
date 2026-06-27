#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
cd "$ROOT"

INSTALL_TOOLS="${QUICKSTART_INSTALL_TOOLS:-0}"
SKIP_VALIDATE="${QUICKSTART_SKIP_VALIDATE:-0}"
SKIP_RAG="${QUICKSTART_SKIP_RAG:-0}"
DIRECT_APPLY="${QUICKSTART_DIRECT_APPLY:-${LOCAL_DIRECT_APPLY:-0}}"

usage() {
  cat <<'EOF'
Usage: scripts/quickstart.sh [options]

Runs the local Private AI Platform Kit lab from toolchain check through gateway
and RAG smoke tests.

Options:
  --install-tools    Run make toolchain-install before checking the local profile.
  --skip-validate    Skip make validate before creating the local cluster.
  --skip-rag         Skip the RAG smoke test.
  --direct-apply     Sync local charts directly with Helm instead of Argo CD.
  -h, --help         Show this help.

Environment:
  QUICKSTART_INSTALL_TOOLS=1  Same as --install-tools.
  QUICKSTART_SKIP_VALIDATE=1  Same as --skip-validate.
  QUICKSTART_SKIP_RAG=1       Same as --skip-rag.
  QUICKSTART_DIRECT_APPLY=1   Same as --direct-apply.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-tools)
      INSTALL_TOOLS=1
      ;;
    --skip-validate)
      SKIP_VALIDATE=1
      ;;
    --skip-rag)
      SKIP_RAG=1
      ;;
    --direct-apply)
      DIRECT_APPLY=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown quickstart option: $1"
      ;;
  esac
  shift
done

on_error() {
  local status=$?
  log "quickstart stopped with status ${status}"
  log "common checks: docker info, kind get clusters, kubectl config current-context"
  log "cluster cleanup: make local-down"
  log "more help: docs/quickstart.md"
  exit "$status"
}
trap on_error ERR

run_step() {
  local label="$1"
  shift
  log "${label}"
  "$@"
}

log "starting guided local lab quickstart"
log "estimated time: 15-30 minutes after container images and the default model are cached"

if [[ "$INSTALL_TOOLS" == "1" ]]; then
  run_step "installing optional validation CLIs into .tools/bin" make toolchain-install
fi

run_step "checking local toolchain profile" make toolchain-doctor TOOLCHAIN_PROFILE=local

if [[ "$SKIP_VALIDATE" != "1" ]]; then
  run_step "running static validation before cluster changes" make validate
else
  log "skipping make validate because QUICKSTART_SKIP_VALIDATE=1"
fi

run_step "creating or reusing local kind cluster" make local-up

if [[ "$DIRECT_APPLY" == "1" ]]; then
  run_step "syncing local charts directly with Helm" env LOCAL_DIRECT_APPLY=1 make sync
else
  run_step "bootstrapping Argo CD" make bootstrap-argocd
  run_step "syncing local GitOps applications" make sync
fi

run_step "running gateway smoke test" make smoke RUNTIME_BACKEND="${RUNTIME_BACKEND:-ollama}"

if [[ "$SKIP_RAG" != "1" ]]; then
  run_step "running RAG smoke test" make rag-smoke
else
  log "skipping RAG smoke because QUICKSTART_SKIP_RAG=1"
fi

log "quickstart completed"
log "try more checks: make sandbox-smoke, make tenant-smoke, make agent-smoke"
log "generate evidence: make evidence LIVE=1"
log "cleanup when done: make local-down"
