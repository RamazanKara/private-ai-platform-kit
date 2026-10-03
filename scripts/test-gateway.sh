#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"

ensure_service_venv "$ROOT/src/inference-gateway"
cd "$ROOT/src/inference-gateway"
PYTHONPATH="$PWD" .venv/bin/python -m pytest -q -s tests

# The Python SDK suite reuses the gateway dev venv (pytest + httpx, no extra lock).
PYTHONPATH="$ROOT/sdk/python" .venv/bin/python -m pytest -q -s "$ROOT/sdk/python/tests"
