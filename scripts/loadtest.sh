#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
require_cmd k6 "Install k6 to run load tests."

cd "$ROOT"
mkdir -p results/loadtest
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="results/loadtest/loadtest-${STAMP}.json"
SUMMARY="results/loadtest/summary-${STAMP}.md"

PLATFORM_API_KEY="${PLATFORM_API_KEY:-local-development-only}" k6 run --summary-export "$OUT" loadtest/chat-completions.js
python3 loadtest/summarize.py "$OUT" "$SUMMARY"
log "wrote ${OUT} and ${SUMMARY}"
