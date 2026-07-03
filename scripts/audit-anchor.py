#!/usr/bin/env python3
"""Emit per-chain head anchors for the gateway's tamper-evident audit log.

Detecting a wholesale re-chain of the audit log (every record rewritten from genesis so the
embedded hashes stay self-consistent) requires an external commitment to each chain's head
hash. This tool reads a gateway JSONL log (file argument or stdin), reuses the same audit
extraction / dedup / grouping / verification as ``scripts/audit-verify.py``, and writes, per
``chain_id``, an anchor record:

    {"chain_id": ..., "count": <records>, "head": <last record_hash>, "ok": <verified>}

Store the output externally (a ConfigMap, an object-store bucket, a SIEM index) on a
schedule. A later ``audit-verify --anchor <file>`` compares freshly observed heads against
this committed file and flags a shrunk chain (rollback/truncation), a changed head
(re-chain/edit), or a missing chain. Anchoring only the head means the committed file is
tiny and append-safe: appending new records advances a chain's head and count; it never
rewrites a previously anchored head.

The tool refuses (exit 1) to anchor a log whose chains do not currently verify, unless
``--allow-broken`` is passed, so an operator does not commit a head that is already tampered.
Exit codes: 0 on success; 1 when a chain fails verification (without --allow-broken); 2 on
usage/IO errors.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Reuse the verifier's parsing and chain logic so anchor and verify never drift. The sibling
# module's filename has a hyphen (audit-verify.py) and cannot be a plain ``import`` target, so
# load it by path relative to this file.
_verify_path = Path(__file__).resolve().parent / "audit-verify.py"
_spec = importlib.util.spec_from_file_location("audit_verify", _verify_path)
if _spec is None or _spec.loader is None:  # pragma: no cover - defensive
    raise SystemExit(f"audit-anchor: cannot load {_verify_path}")
audit_verify = importlib.util.module_from_spec(_spec)
# Register before executing so @dataclass in the loaded module can resolve its own module
# via sys.modules (it looks up cls.__module__); otherwise class creation raises.
sys.modules["audit_verify"] = audit_verify
_spec.loader.exec_module(audit_verify)

deduplicate = audit_verify.deduplicate
extract_audit_events = audit_verify.extract_audit_events
group_into_chains = audit_verify.group_into_chains
read_input = audit_verify.read_input
verify_chain = audit_verify.verify_chain


def build_anchor(lines: list[str]) -> tuple[dict[str, Any], bool]:
    """Return the anchor document and whether every chain verified."""
    chains = group_into_chains(deduplicate(extract_audit_events(lines)))
    entries: dict[str, dict[str, Any]] = {}
    all_ok = True
    for chain in chains:
        result = verify_chain(chain)
        all_ok = all_ok and result.ok
        entries[result.chain_id] = {
            "chain_id": result.chain_id,
            "count": result.count,
            "head": result.head,
            "ok": result.ok,
        }
    document = {
        "kind": "audit-chain-anchor",
        "generated_at": datetime.now(UTC).isoformat(),
        "chains": entries,
    }
    return document, all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit per-chain head anchors for the gateway audit log.")
    parser.add_argument("logfile", nargs="?", help="Gateway JSONL log file, or - / omitted for stdin.")
    parser.add_argument("--output", "-o", metavar="FILE", help="Write the anchor JSON here (default: stdout).")
    parser.add_argument(
        "--allow-broken",
        action="store_true",
        help="Anchor even if a chain fails verification (records the break in the anchor).",
    )
    args = parser.parse_args()

    try:
        lines = read_input(args.logfile)
    except OSError as exc:
        print(f"audit-anchor: cannot read input: {exc}", file=sys.stderr)
        return 2

    document, all_ok = build_anchor(lines)
    rendered = json.dumps(document, indent=2, sort_keys=True) + "\n"

    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)

    if not all_ok and not args.allow_broken:
        print("audit-anchor: refusing to anchor a log with a broken chain (use --allow-broken)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
