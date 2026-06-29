#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${OUT_DIR:-$ROOT/docs/assets/quickstart-screenshots}"
RUN_LIVE="${RUN_LIVE:-0}"

mkdir -p "$OUT_DIR"

run_capture() {
  local name="$1"
  shift
  local output="$OUT_DIR/${name}.txt"
  {
    printf '$'
    printf ' %q' "$@"
    printf '\n\n'
    "$@"
  } >"$output" 2>&1
  printf 'wrote %s\n' "$output"
}

run_capture_optional() {
  local name="$1"
  shift
  local output="$OUT_DIR/${name}.txt"
  {
    printf '$'
    printf ' %q' "$@"
    printf '\n\n'
    "$@" || printf '\noptional capture command exited with status %s\n' "$?"
  } >"$output" 2>&1
  printf 'wrote %s\n' "$output"
}

capture_grafana_dashboard() {
  local output="$OUT_DIR/grafana-dashboard.txt"
  {
    printf '$ kubectl -n monitoring get pods,svc -l app.kubernetes.io/name=grafana -o wide\n\n'
    kubectl -n monitoring get pods,svc -l app.kubernetes.io/name=grafana -o wide
    printf '\n$ kubectl -n monitoring port-forward svc/grafana 13000:80 && curl /api/health\n\n'

    kubectl -n monitoring port-forward svc/grafana 13000:80 >/tmp/private-ai-platform-kit-grafana-port-forward.log 2>&1 &
    local pf_pid="$!"
    local status=1
    for _ in $(seq 1 30); do
      if curl -fsS http://127.0.0.1:13000/api/health; then
        status=0
        printf '\n'
        break
      fi
      sleep 1
    done
    kill "$pf_pid" >/dev/null 2>&1 || true
    return "$status"
  } >"$output" 2>&1 || printf '\noptional capture command exited with status %s\n' "$?" >>"$output"
  printf 'wrote %s\n' "$output"
}

capture_evidence_report() {
  local output="$OUT_DIR/evidence-report.txt"
  {
    printf '$ make evidence LIVE=1\n\n'
    make evidence LIVE=1
    local latest
    latest="$(ls -t results/evidence/evidence-*.md | head -1)"
    printf '\n$ sed -n "1,80p" %s\n\n' "$latest"
    sed -n '1,80p' "$latest"
  } >"$output" 2>&1
  printf 'wrote %s\n' "$output"
}

if [[ "$RUN_LIVE" != "1" ]]; then
  cat <<EOF
This script captures live quickstart proof assets.

Start from a workstation that can run the local lab, then run:

  RUN_LIVE=1 scripts/capture-proof-assets.sh

Output directory:
  $OUT_DIR
EOF
  exit 0
fi

cd "$ROOT"
run_capture quickstart-success make quickstart
run_capture_optional argocd-apps kubectl -n argocd get applications -o wide
capture_grafana_dashboard
run_capture agent-smoke make agent-smoke
capture_evidence_report

if command -v asciinema >/dev/null 2>&1; then
  asciinema rec --overwrite "$OUT_DIR/quickstart.cast" -c "make quickstart"
  printf 'wrote %s\n' "$OUT_DIR/quickstart.cast"
fi
