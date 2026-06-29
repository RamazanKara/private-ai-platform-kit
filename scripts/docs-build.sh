#!/usr/bin/env bash
# Build (or serve) the mkdocs-material documentation site.
#
# Runbooks live at the repo-root runbooks/ because their paths are gate-referenced and
# shipped in live Prometheus alert runbook_url annotations. This script mirrors them into
# docs/runbooks/ (git-ignored) at build time so the site includes and searches them without
# moving the source of truth.
#
#   scripts/docs-build.sh build [--strict]   # build into site/
#   scripts/docs-build.sh serve              # live-reload dev server
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MKDOCS="${MKDOCS:-}"
if [ -z "$MKDOCS" ]; then
  if command -v mkdocs >/dev/null 2>&1; then
    MKDOCS="mkdocs"
  elif [ -x .venv-docs/bin/mkdocs ]; then
    MKDOCS=".venv-docs/bin/mkdocs"
  else
    echo "mkdocs not found. Install it: python3 -m venv .venv-docs && .venv-docs/bin/pip install -r requirements-docs.txt" >&2
    exit 1
  fi
fi

# Mirror the gate-referenced runbooks into the docs tree for the build, and remove the
# mirror on exit so the repo gates (which scan docs/) never see the copies.
cleanup() { rm -rf docs/runbooks; }
trap cleanup EXIT
rm -rf docs/runbooks
mkdir -p docs/runbooks
cp runbooks/*.md docs/runbooks/

CMD="${1:-build}"
shift || true
"$MKDOCS" "$CMD" "$@"
