#!/usr/bin/env bash
# Test-coverage report for both services with enforced minimum floors.
# Reporting aid (not part of the strict release gate): installs hash-pinned pytest-cov +
# coverage (requirements-coverage.lock, --no-deps so the pinned pytest is untouched) on top
# of the hashed dev environment and writes Cobertura XML next to each service.
#
# Override floors with GATEWAY_COVERAGE_MIN / RAG_COVERAGE_MIN.
set -euo pipefail

export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

GATEWAY_COVERAGE_MIN="${GATEWAY_COVERAGE_MIN:-85}"
RAG_COVERAGE_MIN="${RAG_COVERAGE_MIN:-84}"

run_service_coverage() {
  local service="$1" floor="$2"
  local dir="$ROOT/src/${service}"
  echo "[coverage] ${service} (floor ${floor}%)"
  cd "$dir"
  python3 -m venv .venv
  .venv/bin/python -m pip install --require-hashes -r requirements-dev.lock >/dev/null
  .venv/bin/python -m pip install --require-hashes --no-deps -r "$ROOT/requirements-coverage.lock" >/dev/null
  PYTHONPATH="$dir" .venv/bin/python -m pytest -q tests \
    --cov=app \
    --cov-report=term-missing \
    --cov-report="xml:${dir}/coverage.xml" \
    --cov-fail-under="$floor"
}

run_service_coverage inference-gateway "$GATEWAY_COVERAGE_MIN"
run_service_coverage rag-service "$RAG_COVERAGE_MIN"
echo "[coverage] ok"
