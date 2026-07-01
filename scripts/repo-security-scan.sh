#!/usr/bin/env bash
# Canonical repository security scan: Trivy secret + misconfiguration scanning.
# Called by `make repo-security-scan` and scripts/validate.sh so the command
# cannot drift between the two. --helm-kube-version matches the tested kind
# node version (docs/version-matrix.md) so charts render and get scanned
# instead of being skipped for Trivy's default Kubernetes 1.20.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

exec trivy fs \
  --scanners secret,misconfig \
  --severity HIGH,CRITICAL \
  --exit-code 1 \
  --timeout 10m \
  --helm-kube-version 1.31.4 \
  --skip-dirs .tools \
  --skip-dirs results \
  --skip-dirs .out \
  --skip-dirs deploy/policies/kyverno/tests/resources \
  --skip-dirs src/inference-gateway/.venv \
  --skip-dirs src/rag-service/.venv \
  .
