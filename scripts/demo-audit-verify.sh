#!/usr/bin/env bash
set -euo pipefail

# Reproducible terminal cut for the README audit-verify GIF (recorded via
# scripts/demo-audit-verify.tape). Unlike a scripted mock, this runs the REAL
# scripts/audit-verify.py against the committed sample gateway log, so every line
# is genuine tool output: a clean chain verifies, a tampered receipt is caught, and
# a head anchor catches a rolled-back (truncated) log. Run it yourself with
# `make audit-verify-demo`; point `make audit-verify AUDIT_LOG=...` at real pod logs.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WORK="/tmp/audit-verify-demo"
rm -rf "$WORK" && mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT
LOG="results/sample-gateway-audit.log"
TAMPERED="$WORK/tampered.log"
TRUNCATED="$WORK/truncated.log"
ANCHOR="$WORK/anchor.json"

say() { printf '\033[36m%s\033[0m\n' "$1"; sleep "${2:-0.6}"; }
# The tamper/rollback verifications intentionally exit non-zero; tolerate it so the
# narration continues (this is a demo cut, not a gate).
run() { printf '\033[2m$ %s\033[0m\n' "$1"; sleep 0.5; eval "$1" || true; sleep "${2:-0.9}"; }

say "# The gateway writes every governed call as a hash-linked receipt. Verify one:"
run "python3 scripts/audit-verify.py $LOG"

say "# Now an attacker edits a stored receipt but keeps its record_hash..."
python3 - "$LOG" "$TAMPERED" <<'PY'
import json, sys

src, dst = sys.argv[1], sys.argv[2]
lines = open(src, encoding="utf-8").read().splitlines()
order = []
for line in lines:
    brace = line.find("{")
    if brace < 0:
        continue
    try:
        record = json.loads(line[brace:])
    except ValueError:
        continue
    digest = record.get("record_hash")
    if digest and digest not in order:
        order.append(digest)
target = order[1]
out = []
for line in lines:
    brace = line.find("{")
    if brace >= 0:
        try:
            record = json.loads(line[brace:])
        except ValueError:
            record = None
        if record and record.get("record_hash") == target and "sandbox_id" in record:
            record["sandbox_id"] = f"{record['sandbox_id']}-EVIL"  # edit hashed content, keep the hash
            line = line[:brace] + json.dumps(record)
    out.append(line)
open(dst, "w", encoding="utf-8").write("\n".join(out) + "\n")
PY
run "python3 scripts/audit-verify.py $TAMPERED" 1.0

say "# And an anchor of the chain head catches a rolled-back (truncated) log:"
run "python3 scripts/audit-anchor.py $LOG --output $ANCHOR"
head -n -2 "$LOG" > "$TRUNCATED"
run "python3 scripts/audit-verify.py --anchor $ANCHOR $TRUNCATED" 1.0

say "# Same tool an auditor runs offline against exported logs. Receipts you can prove."
