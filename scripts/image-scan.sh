#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
cd "$ROOT"

TRIVY_BIN="${TRIVY_BIN:-trivy}"
IMAGE_PREFIX="${IMAGE_PREFIX:-private-ai-platform-kit}"
GATEWAY_IMAGE="${GATEWAY_IMAGE:-${IMAGE_PREFIX}/inference-gateway:local-scan}"
RAG_IMAGE="${RAG_IMAGE:-${IMAGE_PREFIX}/rag-service:local-scan}"

if ! has_cmd "$TRIVY_BIN" && [[ "$TRIVY_BIN" == "trivy" && -x "$ROOT/.tools/bin/trivy" ]]; then
  TRIVY_BIN="$ROOT/.tools/bin/trivy"
fi

require_cmd docker "Docker is required to build local images for vulnerability scans."
require_cmd "$TRIVY_BIN" "Trivy is required to scan local images. Run INSTALL_TOOLS=trivy make toolchain-install."

log "building inference gateway image for vulnerability scan"
docker build --pull -t "$GATEWAY_IMAGE" services/inference-gateway

log "building RAG service image for vulnerability scan"
docker build --pull -t "$RAG_IMAGE" services/rag-service

scan_image() {
  local image="$1"
  log "scanning ${image} for HIGH and CRITICAL vulnerabilities"
  "$TRIVY_BIN" image \
    --scanners vuln \
    --severity HIGH,CRITICAL \
    --exit-code 1 \
    "$image"
}

scan_image "$GATEWAY_IMAGE"
scan_image "$RAG_IMAGE"

log "image vulnerability scans completed"
