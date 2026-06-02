#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
cd "$ROOT"

TRIVY_BIN="${TRIVY_BIN:-trivy}"
SYFT_BIN="${SYFT_BIN:-syft}"
IMAGE_PREFIX="${IMAGE_PREFIX:-private-ai-platform-kit}"
GATEWAY_IMAGE="${GATEWAY_IMAGE:-${IMAGE_PREFIX}/inference-gateway:local-scan}"
RAG_IMAGE="${RAG_IMAGE:-${IMAGE_PREFIX}/rag-service:local-scan}"
OUTPUT_DIR="${OUTPUT_DIR:-results/supply-chain}"
STAMP="${STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"

require_cmd docker "Docker is required to build local images for vulnerability scans."
require_cmd "$TRIVY_BIN" "Trivy is required to scan local images. Run INSTALL_TOOLS=trivy make toolchain-install."
require_cmd "$SYFT_BIN" "Syft is required to generate image SBOM evidence. Run INSTALL_TOOLS=syft make toolchain-install."
require_cmd python3 "Python 3 is required to write supply-chain evidence summaries."

mkdir -p "$OUTPUT_DIR"
GATEWAY_SBOM="${OUTPUT_DIR}/inference-gateway-${STAMP}.spdx.json"
RAG_SBOM="${OUTPUT_DIR}/rag-service-${STAMP}.spdx.json"
GATEWAY_SARIF="${OUTPUT_DIR}/trivy-inference-gateway-${STAMP}.sarif"
RAG_SARIF="${OUTPUT_DIR}/trivy-rag-service-${STAMP}.sarif"
CHECKSUMS="${OUTPUT_DIR}/supply-chain-checksums-${STAMP}.txt"
SUMMARY_JSON="${OUTPUT_DIR}/supply-chain-summary-${STAMP}.json"
SUMMARY_MD="${OUTPUT_DIR}/supply-chain-summary-${STAMP}.md"

log "building inference gateway image for vulnerability scan"
docker build --pull -t "$GATEWAY_IMAGE" services/inference-gateway

log "building RAG service image for vulnerability scan"
docker build --pull -t "$RAG_IMAGE" services/rag-service

scan_image() {
  local image="$1"
  local output="$2"
  log "scanning ${image} for HIGH and CRITICAL vulnerabilities"
  "$TRIVY_BIN" image \
    --scanners vuln \
    --severity HIGH,CRITICAL \
    --exit-code 1 \
    --format sarif \
    --output "$output" \
    "$image"
}

write_sbom() {
  local image="$1"
  local output="$2"
  log "generating SBOM for ${image}"
  "$SYFT_BIN" "$image" -o spdx-json >"$output"
}

write_summary() {
  sha256sum \
    "$GATEWAY_SBOM" \
    "$RAG_SBOM" \
    "$GATEWAY_SARIF" \
    "$RAG_SARIF" \
    >"$CHECKSUMS"

  python3 - "$SUMMARY_JSON" "$STAMP" "$GATEWAY_IMAGE" "$RAG_IMAGE" "$GATEWAY_SBOM" "$RAG_SBOM" "$GATEWAY_SARIF" "$RAG_SARIF" "$CHECKSUMS" <<'PY'
import json
import sys
from pathlib import Path

summary, stamp, gateway_image, rag_image, gateway_sbom, rag_sbom, gateway_sarif, rag_sarif, checksums = sys.argv[1:]
payload = {
    "project": "Private AI Platform Kit",
    "generated_at": stamp,
    "gate": "HIGH and CRITICAL image vulnerabilities must be zero",
    "status": "pass",
    "images": [
        {"name": "inference-gateway", "image": gateway_image, "sbom": gateway_sbom, "trivy_sarif": gateway_sarif},
        {"name": "rag-service", "image": rag_image, "sbom": rag_sbom, "trivy_sarif": rag_sarif},
    ],
    "checksums": checksums,
}
Path(summary).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY

  cat >"$SUMMARY_MD" <<EOF
# Supply-Chain Scan Summary

Generated: \`${STAMP}\`

Status: pass

Gate: HIGH and CRITICAL image vulnerabilities must be zero.

| Image | SBOM | Trivy SARIF |
| --- | --- | --- |
| \`${GATEWAY_IMAGE}\` | \`${GATEWAY_SBOM}\` | \`${GATEWAY_SARIF}\` |
| \`${RAG_IMAGE}\` | \`${RAG_SBOM}\` | \`${RAG_SARIF}\` |

Checksums: \`${CHECKSUMS}\`
EOF
}

write_sbom "$GATEWAY_IMAGE" "$GATEWAY_SBOM"
write_sbom "$RAG_IMAGE" "$RAG_SBOM"
scan_image "$GATEWAY_IMAGE" "$GATEWAY_SARIF"
scan_image "$RAG_IMAGE" "$RAG_SARIF"
write_summary
python3 scripts/supply-chain-evidence.py --summary "$SUMMARY_JSON" --strict-current --check

log "image vulnerability scans completed; wrote ${SUMMARY_MD}"
