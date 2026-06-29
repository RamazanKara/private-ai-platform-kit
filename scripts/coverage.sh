#!/usr/bin/env bash
# Test-coverage report for both services with enforced minimum floors.
# Reporting aid (not part of the strict release gate): installs pytest-cov on top of
# the hashed dev environment and writes Cobertura XML next to each service.
#
# Override floors with GATEWAY_COVERAGE_MIN / RAG_COVERAGE_MIN.
set -euo pipefail

export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PYTEST_COV_VERSION="${PYTEST_COV_VERSION:-7.1.0}"
GATEWAY_COVERAGE_MIN="${GATEWAY_COVERAGE_MIN:-85}"
RAG_COVERAGE_MIN="${RAG_COVERAGE_MIN:-84}"

run_service_coverage() {
  local service="$1" floor="$2"
  local dir="$ROOT/src/${service}"
  echo "[coverage] ${service} (floor ${floor}%)"
  cd "$dir"
  python3 -m venv .venv
  .venv/bin/python -m pip install --require-hashes -r requirements-dev.lock >/dev/null
  .venv/bin/python -m pip install "pytest-cov==${PYTEST_COV_VERSION}" >/dev/null
  PYTHONPATH="$dir" .venv/bin/python -m pytest -q tests \
    --cov=app \
    --cov-report=term-missing \
    --cov-report="xml:${dir}/coverage.xml" \
    --cov-fail-under="$floor"
}

run_service_coverage inference-gateway "$GATEWAY_COVERAGE_MIN"
run_service_coverage rag-service "$RAG_COVERAGE_MIN"
echo "[coverage] ok"
