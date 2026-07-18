#!/usr/bin/env bash
set -euo pipefail

# Staged terminal cut for the README GIF, recorded via scripts/demo.tape.
# It illustrates the live workflow without claiming to be current evidence.
# Run `make agent-sandbox-demo` for the end-to-end result from a real cluster.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say() {
  printf '%s\n' "$1"
  sleep "${2:-0.5}"
}

printf '$ make agent-sandbox-demo\n'
sleep 0.8
say "==> step 1/4: agent-sandbox controller (vendored, checksummed) ... ready" 0.9
say "==> step 2/4: hardened workspace" 0.6
say "sandbox agent-lab: Ready: non-root, read-only rootfs, no service-account token" 0.9
say "projected credential ok (JWT, audience-bound, auto-rotated, no long-lived secrets)" 1.0
say "probing non-catalog egress (198.51.100.10) ... BLOCKED (default-deny + approved catalog)" 1.2
say "==> step 3/4: real coding agent (aider) through the governed gateway" 0.9
say 'receipt {"action_type":"model_call","decision":"allowed","sandbox_id":"agent-lab"}' 1.0
say 'receipt {"action_type":"model_call","decision":"denied","error":"model not on the approved allowlist"}' 1.2
say "==> step 4/4: evidence pack (hash-chained receipts, EU AI Act / NIST / ISO 42001 crosswalk)" 1.0
say "evidence pack written: results/evidence/evidence-20260702T041519Z.md, all controls green" 1.4
say "" 0.2
say "isolated workspace · fail-closed egress · receipts on record" 2.2
