#!/usr/bin/env bash
# Static code-quality gate: Ruff lint, Ruff format check, and mypy type checks.
# Tools run from an isolated, hash-pinned .venv-quality so they never perturb the
# runtime or dev dependency locks. Invoked by scripts/validate.sh and the Make
# targets lint/format/format-check/typecheck/quality.
#
# Usage: scripts/quality.sh [all|lint|format|format-check|typecheck]
set -euo pipefail

export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="${1:-all}"
VENV="$ROOT/.venv-quality"
PYBIN="$VENV/bin/python"

ensure_tools() {
  # Rebuild when the lock changes, not only when the venv is missing: otherwise a
  # ruff/mypy bump never reaches an existing checkout and local results drift from CI.
  local digest
  digest="$(sha256sum requirements-quality.lock | cut -d' ' -f1)"
  if [[ ! -x "$PYBIN" ]] || [[ "$(cat "$VENV/.lock-sha256" 2>/dev/null)" != "$digest" ]]; then
    echo "[quality] creating isolated tool environment"
    rm -rf "$VENV"
    python3 -m venv "$VENV"
    "$PYBIN" -m pip install --require-hashes -r requirements-quality.lock >/dev/null
    echo "$digest" >"$VENV/.lock-sha256"
  fi
}

run_lint() {
  echo "[quality] ruff lint (src + scripts)"
  "$PYBIN" -m ruff check .
}

run_format_check() {
  echo "[quality] ruff format check (src)"
  "$PYBIN" -m ruff format --check src
}

run_format_fix() {
  echo "[quality] ruff format + autofix (src)"
  "$PYBIN" -m ruff format src
  "$PYBIN" -m ruff check . --fix
}

run_typecheck() {
  # Each service exposes a top-level `app` package, so mypy must run once per service
  # from the service root rather than in a single invocation.
  for service in inference-gateway rag-service; do
    echo "[quality] mypy (${service})"
    (
      cd "src/${service}"
      MYPYPATH="$PWD" "$PYBIN" -m mypy app --config-file "$ROOT/pyproject.toml"
    )
  done
  echo "[quality] mypy (python SDK)"
  "$PYBIN" -m mypy sdk/python/ai_platform_client --config-file "$ROOT/pyproject.toml"
}

ensure_tools
case "$MODE" in
  all) run_lint; run_format_check; run_typecheck ;;
  lint) run_lint ;;
  format) run_format_fix ;;
  format-check) run_format_check ;;
  typecheck) run_typecheck ;;
  *)
    echo "usage: scripts/quality.sh [all|lint|format|format-check|typecheck]" >&2
    exit 2
    ;;
esac
echo "[quality] ok (${MODE})"
