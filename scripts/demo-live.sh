#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

section() {
  printf '\n==> %s\n' "$1"
}

run() {
  printf '\n$ %s\n' "$*"
  "$@"
}

section "AI Platform Ops Lab live release demo"
printf 'Local-first private LLM and coding-agent platform for Kubernetes.\n'
printf 'This run uses real repository checks; no cluster is required.\n'

section "Repository map"
run find charts services runbooks governance slo chaos -maxdepth 2 -type f

section "Production readiness"
run make production-check

section "Customer evidence controls"
run services/inference-gateway/.venv/bin/python scripts/evidence-pack.py --check

section "Release gate"
run services/inference-gateway/.venv/bin/python scripts/release-gate.py --check

section "Demo complete"
printf 'Public release is ready when git commit, tag, push, and GitHub release creation succeed.\n'
