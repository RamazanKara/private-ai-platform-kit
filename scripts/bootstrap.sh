#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"

require_cmd docker "Install Docker Engine/Desktop and ensure 'docker info' succeeds."
require_cmd python3 "Install Python 3.12 or newer."
require_cmd curl "curl is required to install the pinned workstation CLIs."

if ! docker info >/dev/null 2>&1; then
  die "Docker is installed but the daemon is unavailable; start Docker and retry"
fi

log "installing pinned local and validation CLIs into .tools/bin"
INSTALL_TOOLS="kind kubectl helm kubeconform kyverno k6 syft argocd cosign trivy" \
  "$ROOT/scripts/install-validation-tools.sh" --bin-dir "$ROOT/.tools/bin"

log "checking the local workstation profile"
python3 "$ROOT/scripts/toolchain-doctor.py" --profile local --check

log "starting the guided local platform quickstart"
exec "$ROOT/scripts/quickstart.sh"
