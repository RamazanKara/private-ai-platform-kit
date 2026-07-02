#!/usr/bin/env bash
set -euo pipefail

export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/src/inference-gateway"

python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock >/dev/null
PYTHONPATH="$PWD" .venv/bin/python -m pytest -q -s tests

# The Python SDK suite reuses the gateway dev venv (pytest + httpx, no extra lock).
PYTHONPATH="$ROOT/sdk/python" .venv/bin/python -m pytest -q -s "$ROOT/sdk/python/tests"
