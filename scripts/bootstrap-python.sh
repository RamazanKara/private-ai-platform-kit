#!/usr/bin/env bash
set -euo pipefail

export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/services/inference-gateway"

python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock >/dev/null
