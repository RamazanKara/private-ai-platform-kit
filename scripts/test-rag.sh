#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"

ensure_service_venv "$ROOT/src/rag-service"
cd "$ROOT/src/rag-service"
PYTHONPATH="$PWD" .venv/bin/python -m pytest -q -s tests
