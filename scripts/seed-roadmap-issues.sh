#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CREATE=0
REPO="${GITHUB_REPOSITORY:-RamazanKara/private-ai-platform-kit}"

usage() {
  cat <<'USAGE'
Usage: scripts/seed-roadmap-issues.sh [--create] [--repo owner/name]

Prints the roadmap issue seed set by default. Pass --create to call gh issue create.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --create)
      CREATE=1
      shift
      ;;
    --repo)
      REPO="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$CREATE" == "1" ]] && ! command -v gh >/dev/null 2>&1; then
  echo "gh is required when --create is set" >&2
  exit 1
fi

seed_issue() {
  local title="$1"
  local labels="$2"
  local body="$3"
  if [[ "$CREATE" == "1" ]]; then
    gh issue create --repo "$REPO" --title "$title" --label "$labels" --body "$body"
  else
    printf 'gh issue create --repo %q --title %q --label %q --body %q\n' \
      "$REPO" "$title" "$labels" "$body"
  fi
}

seed_issue \
  "Add quickstart screenshots and terminal recordings" \
  "docs,good first issue,help wanted" \
  "Capture quickstart success, Argo CD apps, Grafana dashboard, and evidence report assets. Update docs/proof.md and docs/quickstart.md with the recorded artifacts."

seed_issue \
  "Publish current strict evidence for the next release" \
  "docs,help wanted" \
  "Run validate-full, image-scan, supply-chain-check, loadtest-local, evidence, and release-gate-strict. Attach the generated evidence summary to the release notes."

seed_issue \
  "Add enterprise IdP examples and JWKS rotation drills" \
  "runtime,security,help wanted" \
  "Add Okta, Entra ID, and Keycloak examples for gateway JWT auth, plus documented JWKS rotation drills and failure-mode tests."

seed_issue \
  "Expand streaming compatibility tests for Ollama and vLLM" \
  "runtime,helm,help wanted" \
  "Add runtime compatibility tests for streaming chat responses from Ollama and vLLM, including cancellation and malformed event handling."

seed_issue \
  "Add Qdrant migration dry-run and rollback walkthrough" \
  "rag,help wanted" \
  "Document collection-version dry runs, rollback, and old-vector cleanup for embedding-model or chunking-policy migrations."

seed_issue \
  "Add chart install profile examples" \
  "helm,docs,good first issue" \
  "Add minimal, local, and customer install profile examples to the chart READMEs, including OCI pull and Helm template commands."

seed_issue \
  "Document regulated offline tenant example" \
  "tenant,docs,good first issue" \
  "Turn the regulated offline tenant spec into a walkthrough with generated manifests, expected policies, and smoke-test commands."

seed_issue \
  "Document GPU-backed coding-agent tenant example" \
  "tenant,runtime,docs,help wanted" \
  "Add a GPU-backed coding-agent tenant walkthrough that uses the vLLM customer profile and agent workspace controls."

seed_issue \
  "Add Scorecard triage and remediation guidance" \
  "security,help wanted" \
  "Document how maintainers review OpenSSF Scorecard SARIF, decide accepted risk, and track remediation work."

printf '\nRoadmap source: %s\n' "$ROOT/ROADMAP.md"
