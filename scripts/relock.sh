#!/usr/bin/env bash
# Regenerate every hash-pinned Python lock from its requirement input.
#
# Each lock is rebuilt with the exact pip-compile flags recorded in its header, from an
# isolated, hash-pinned pip-tools environment (.venv-relock). Run it after editing any
# requirements*.txt / *.in file, including on a Dependabot branch, which only edits the
# inputs and would otherwise fail `make dependency-lock-check`.
#
# Usage:
#   scripts/relock.sh                  Re-resolve locks, keeping existing transitive pins
#   scripts/relock.sh --upgrade        Also move transitive dependencies to their newest release
#   scripts/relock.sh -P urllib3==2.8.0  Upgrade one transitive dependency (repeatable)
#
# Locks are resolved on Python 3.12, the oldest interpreter the SDK test matrix shares with
# the services. Resolving on 3.13+ drops pins that only older interpreters need (for example
# typing-extensions for anyio), which would break the 3.11/3.12 legs under --require-hashes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RELOCK_PYTHON="${RELOCK_PYTHON:-python3.12}"
VENV="$ROOT/.venv-relock"

if ! command -v "$RELOCK_PYTHON" >/dev/null 2>&1; then
  if python3 -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))' 2>/dev/null; then
    RELOCK_PYTHON=python3
  else
    echo "[relock] Python 3.12 is required (set RELOCK_PYTHON=/path/to/python3.12)." >&2
    exit 1
  fi
fi

lock_digest="$(sha256sum requirements-relock.lock | cut -d' ' -f1)"
if [[ ! -x "$VENV/bin/pip-compile" || "$(cat "$VENV/.lock-sha256" 2>/dev/null)" != "$lock_digest" ]]; then
  echo "[relock] creating isolated pip-tools environment with ${RELOCK_PYTHON}"
  rm -rf "$VENV"
  "$RELOCK_PYTHON" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet --require-hashes -r requirements-relock.lock
  echo "$lock_digest" >"$VENV/.lock-sha256"
fi

compile() {
  local output="$1"
  shift
  echo "[relock] ${output}"
  "$VENV/bin/pip-compile" --quiet --generate-hashes "$@" --output-file="$output"
  # pip-tools 7.x records a spurious --no-index in the header; a reader who copies that
  # command would get a resolver that cannot reach PyPI.
  sed -i '/^#    pip-compile /s/ --no-index//' "$output"
}

# Service runtime and dev locks; the dev lock extends the runtime input with -r.
for service in inference-gateway rag-service; do
  for name in requirements requirements-dev; do
    compile "src/${service}/${name}.lock" --allow-unsafe --no-strip-extras "$@" "src/${service}/${name}.txt"
  done
done
compile requirements-quality.lock --allow-unsafe --no-strip-extras "$@" requirements-quality.txt
compile requirements-sdk-build.lock --no-strip-extras "$@" requirements-sdk-build.in
compile requirements-sdk-test.lock --no-strip-extras "$@" requirements-sdk-test.in
compile requirements-docs.txt --no-strip-extras "$@" requirements-docs.in
compile requirements-relock.lock --allow-unsafe --no-strip-extras "$@" requirements-relock.in

python3 scripts/repo-hygiene.py --check
echo "[relock] ok"
