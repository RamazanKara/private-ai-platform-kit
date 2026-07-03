#!/usr/bin/env python3
"""Operator verifier for the gateway's tamper-evident audit hash chain.

The inference gateway links each redacted audit event into a per-process SHA-256 hash
chain (ADR 0006): ``h_0 = SHA-256("genesis")`` and, for each record,
``record_hash = SHA-256(prev_hash || canonical(record))`` where ``canonical`` is
``json.dumps(record, sort_keys=True, separators=(",", ":"))`` computed over the event
*before* the ``prev_hash`` and ``record_hash`` fields are stamped on. Each event is logged
twice (to the audit logger and to ``uvicorn.error``), so a pod-log stream carries two
byte-identical copies of every record.

This tool reads a gateway JSONL log (file argument or stdin), extracts the audit events,
deduplicates the double-logged copies, groups records into their per-process chains, and
**verifies the embedded chain**: it recomputes each ``record_hash`` and checks the
``prev_hash`` linkage back to genesis. Unlike ``paper/evidence-model/audit_chain.py`` (which
re-chains from genesis for the evidence demo), this verifier checks the hashes the gateway
actually emitted, so any edit, insertion, deletion, or reordering of emitted records is
reported and exits non-zero.

Grouping:
  - Records carrying ``chain_id`` (gateway >= v0.20.0) group by that field; each chain
    starts at genesis independently.
  - Records lacking ``chain_id`` (pre-v0.20.0) are grouped by genesis-restart boundary: a
    new segment begins at each record whose ``prev_hash`` equals genesis.

Anchoring (``--anchor <file>``):
  Detecting a wholesale re-chain (every record rewritten from genesis) needs an external
  commitment to each chain head. ``scripts/audit-anchor.py`` writes such a file
  ({chain_id, count, last record_hash} per chain); with ``--anchor`` this verifier compares
  the currently observed heads against that committed file and flags a shrunk chain (rollback
  / truncation), a changed head (re-chain / edit), or a missing chain.

Exit codes: 0 when every chain verifies (and, with --anchor, matches); 1 on any break or
mismatch; 2 on usage/IO errors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# h_0 = SHA-256("genesis"); must match the gateway's AUDIT_GENESIS and the paper reference.
GENESIS = hashlib.sha256(b"genesis").hexdigest()
AUDIT_EVENTS = {"inference_request", "batch_request"}
CHAIN_FIELDS = ("prev_hash", "record_hash")
# Sentinel chain-id for pre-v0.20.0 records that carry no chain_id field. Kept distinct from
# any pod-derived HOSTNAME:ts so genesis-restart grouping cannot collide with a real chain.
LEGACY_CHAIN_ID = "<legacy-no-chain-id>"


def canonical(record: dict[str, Any]) -> bytes:
    """Canonical form the gateway hashes: compact, key-sorted JSON of the record."""
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_record_hash(prev_hash: str, record: dict[str, Any]) -> str:
    """Recompute record_hash = SHA-256(prev_hash_ascii || canonical(record))."""
    return hashlib.sha256(prev_hash.encode("ascii") + canonical(record)).hexdigest()


def extract_audit_events(lines: list[str]) -> list[dict[str, Any]]:
    """Parse gateway log lines into audit-event dicts.

    A gateway log line may carry a logging prefix before the JSON payload; take the text
    from the first ``{``. Keep only objects that are audit events (``event`` in
    ``AUDIT_EVENTS``) and carry a ``record_hash`` (chain-linked).
    """
    events: list[dict[str, Any]] = []
    for line in lines:
        brace = line.find("{")
        if brace < 0:
            continue
        try:
            obj = json.loads(line[brace:])
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("event") in AUDIT_EVENTS and "record_hash" in obj:
            events.append(obj)
    return events


def divergent_duplicates(events: list[dict[str, Any]]) -> list[str]:
    """Return record_hash values carried by two lines with different content.

    The gateway double-logs each event byte-identically, so two lines sharing a
    ``record_hash`` but differing anywhere else means one copy was tampered while its
    ``record_hash`` was left intact. Detecting this before :func:`deduplicate` (which keeps
    the first copy of each ``record_hash``) stops a tampered duplicate from hiding behind a
    clean one.
    """
    signatures: dict[str, str] = {}
    conflicts: set[str] = set()
    for event in events:
        digest = event["record_hash"]
        signature = json.dumps(
            {key: value for key, value in event.items() if key != "record_hash"},
            sort_keys=True,
            separators=(",", ":"),
        )
        if digest in signatures:
            if signatures[digest] != signature:
                conflicts.add(digest)
        else:
            signatures[digest] = signature
    return sorted(conflicts)


def deduplicate(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop the double-logged copies, keyed by record_hash (records are byte-identical).

    Order is preserved by first appearance so the recovered per-chain sequence matches the
    order the gateway advanced the chain. Callers should run :func:`divergent_duplicates`
    first: this keeps the first copy of each ``record_hash`` and cannot, on its own, tell a
    clean duplicate from a tampered one that kept the same hash.
    """
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for event in events:
        digest = event["record_hash"]
        if digest in seen:
            continue
        seen.add(digest)
        unique.append(event)
    return unique


@dataclass
class Chain:
    chain_id: str
    records: list[dict[str, Any]] = field(default_factory=list)


def group_into_chains(events: list[dict[str, Any]]) -> list[Chain]:
    """Group deduplicated events into per-process chains.

    Records with a ``chain_id`` group by it (each an independent genesis-rooted chain).
    Records without one (pre-v0.20.0) are split into segments at genesis-restart
    boundaries: a record whose ``prev_hash`` == genesis starts a new legacy segment.
    """
    chains: list[Chain] = []
    by_id: dict[str, Chain] = {}
    legacy_current: Chain | None = None
    for event in events:
        chain_id = event.get("chain_id")
        if isinstance(chain_id, str) and chain_id:
            chain = by_id.get(chain_id)
            if chain is None:
                chain = Chain(chain_id=chain_id)
                by_id[chain_id] = chain
                chains.append(chain)
            chain.records.append(event)
            continue
        # Legacy record: start a new segment whenever we see a genesis-rooted record.
        if legacy_current is None or event.get("prev_hash") == GENESIS:
            legacy_current = Chain(chain_id=LEGACY_CHAIN_ID)
            chains.append(legacy_current)
        legacy_current.records.append(event)
    return chains


@dataclass
class ChainResult:
    chain_id: str
    count: int
    ok: bool
    head: str
    reason: str | None = None
    position: int | None = None


def verify_chain(chain: Chain) -> ChainResult:
    """Verify one chain's embedded hashes and report the first broken position."""
    prev = GENESIS
    for position, record in enumerate(chain.records):
        embedded_prev = record.get("prev_hash")
        embedded_hash = record.get("record_hash")
        if embedded_prev != prev:
            reason = "prev_hash_not_genesis" if position == 0 else "broken_link_or_reordered"
            return ChainResult(chain.chain_id, len(chain.records), False, prev, reason, position)
        payload = {k: v for k, v in record.items() if k not in CHAIN_FIELDS}
        recomputed = compute_record_hash(prev, payload)
        if recomputed != embedded_hash:
            return ChainResult(chain.chain_id, len(chain.records), False, prev, "record_hash_mismatch", position)
        prev = embedded_hash
    return ChainResult(chain.chain_id, len(chain.records), True, prev)


def observed_heads(results: list[ChainResult]) -> dict[str, dict[str, Any]]:
    """Head summary per chain: {chain_id: {count, head}} for anchoring."""
    return {r.chain_id: {"count": r.count, "head": r.head} for r in results}


def compare_anchor(current: dict[str, dict[str, Any]], anchored: dict[str, dict[str, Any]]) -> list[str]:
    """Flag rollback / re-chain / missing-chain against a previously anchored head file.

    A chain that is absent now, shorter than anchored, or whose head changed while the count
    did not grow all indicate the log was rewritten rather than merely appended to.
    """
    problems: list[str] = []
    for chain_id, anchor in anchored.items():
        now = current.get(chain_id)
        if now is None:
            problems.append(f"{chain_id}: chain missing (was {anchor['count']} record(s)); possible rollback/deletion")
            continue
        if now["count"] < anchor["count"]:
            problems.append(
                f"{chain_id}: chain shrank {anchor['count']} -> {now['count']} record(s); possible truncation/rollback"
            )
            continue
        if now["count"] == anchor["count"] and now["head"] != anchor["head"]:
            problems.append(f"{chain_id}: head changed without growth; records were rewritten (re-chain)")
    return problems


def load_anchor_file(path: Path) -> dict[str, dict[str, Any]]:
    """Load an anchor file (audit-anchor.py output) into {chain_id: {count, head}}."""
    data = json.loads(path.read_text(encoding="utf-8"))
    chains = data.get("chains", data) if isinstance(data, dict) else {}
    normalized: dict[str, dict[str, Any]] = {}
    for chain_id, entry in chains.items():
        normalized[chain_id] = {"count": int(entry["count"]), "head": str(entry["head"])}
    return normalized


def read_input(source: str | None) -> list[str]:
    if source is None or source == "-":
        return sys.stdin.read().splitlines()
    return Path(source).read_text(encoding="utf-8", errors="ignore").splitlines()


def selftest() -> int:
    """Build a valid chain with the gateway's exact canonicalization, verify, then tamper.

    The gateway's ``_chain_audit_event`` is intentionally not imported here (it pulls the
    full FastAPI app and its runtime dependencies into a standalone operator script); the
    canonicalization is replicated above and a gateway unit test
    (``test_audit_verify_canonicalization_matches_gateway``) asserts the two agree
    byte-for-byte, so drift is caught by the quality gate rather than silently.
    """

    def build(records: list[dict[str, Any]], chain_id: str) -> list[dict[str, Any]]:
        prev = GENESIS
        emitted: list[dict[str, Any]] = []
        for record in records:
            event = dict(record)
            event["chain_id"] = chain_id
            digest = compute_record_hash(prev, event)
            event["prev_hash"] = prev
            event["record_hash"] = digest
            emitted.append(event)
            prev = digest
        return emitted

    raw = [
        {"event": "inference_request", "request_id": "req-0", "ts": 1.0, "status_code": 200},
        {"event": "batch_request", "request_id": "req-1", "ts": 2.0, "status_code": 200},
        {"event": "inference_request", "request_id": "req-2", "ts": 3.0, "status_code": 429},
    ]
    events = build(raw, "selftest-chain:1")

    # A valid chain, double-logged (each record twice), must dedup and verify clean.
    doubled = [line for event in events for line in (event, dict(event))]
    lines = [json.dumps(event, sort_keys=True) for event in doubled]
    chains = group_into_chains(deduplicate(extract_audit_events(lines)))
    assert len(chains) == 1, f"expected 1 chain, got {len(chains)}"
    result = verify_chain(chains[0])
    assert result.ok, f"valid chain failed verification: {result.reason} at {result.position}"
    assert result.count == 3, f"dedup should leave 3 records, got {result.count}"

    # Mutate one record's covered field, leaving its stored hash: must be detected.
    tampered = [dict(event) for event in events]
    tampered[1]["status_code"] = 500
    tampered_lines = [json.dumps(event, sort_keys=True) for event in tampered]
    tampered_chains = group_into_chains(deduplicate(extract_audit_events(tampered_lines)))
    tampered_result = verify_chain(tampered_chains[0])
    assert not tampered_result.ok, "tamper on a covered field was not detected"
    assert tampered_result.reason == "record_hash_mismatch", tampered_result.reason
    assert tampered_result.position == 1, tampered_result.position

    # Reordering two records breaks the prev_hash linkage: must be detected.
    reordered = [events[0], events[2], events[1]]
    reordered_lines = [json.dumps(event, sort_keys=True) for event in reordered]
    reordered_chains = group_into_chains(deduplicate(extract_audit_events(reordered_lines)))
    reordered_result = verify_chain(reordered_chains[0])
    assert not reordered_result.ok, "reordering was not detected"

    # Legacy grouping: records without chain_id split at genesis restarts.
    legacy = [dict(event) for event in events]
    for event in legacy:
        event.pop("chain_id", None)
    # Re-chain legacy copies from genesis (no chain_id) so hashes stay self-consistent.
    legacy_prev = GENESIS
    for event in legacy:
        payload = {k: v for k, v in event.items() if k not in CHAIN_FIELDS}
        event["prev_hash"] = legacy_prev
        event["record_hash"] = compute_record_hash(legacy_prev, payload)
        legacy_prev = event["record_hash"]
    legacy_lines = [json.dumps(event, sort_keys=True) for event in legacy]
    legacy_chains = group_into_chains(deduplicate(extract_audit_events(legacy_lines)))
    assert len(legacy_chains) == 1, f"legacy segment grouping wrong: {len(legacy_chains)}"
    assert verify_chain(legacy_chains[0]).ok, "legacy chain failed verification"

    # Anchor: a shrunk chain and a re-chained head are both flagged.
    heads = observed_heads([verify_chain(chains[0])])
    shrunk = compare_anchor(
        {"selftest-chain:1": {"count": 1, "head": events[0]["record_hash"]}},
        {"selftest-chain:1": {"count": 3, "head": events[-1]["record_hash"]}},
    )
    assert shrunk and "shrank" in shrunk[0], shrunk
    rechained_head = compute_record_hash(GENESIS, {"event": "inference_request", "x": 1})
    rechain = compare_anchor(
        {"selftest-chain:1": {"count": 3, "head": rechained_head}},
        {"selftest-chain:1": {"count": 3, "head": events[-1]["record_hash"]}},
    )
    assert rechain and "rewritten" in rechain[0], rechain
    assert not compare_anchor(heads, heads), "identical heads must not flag"

    print("audit-verify selftest OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the gateway's tamper-evident audit hash chain.")
    parser.add_argument("logfile", nargs="?", help="Gateway JSONL log file, or - / omitted for stdin.")
    parser.add_argument(
        "--anchor", metavar="FILE", help="Compare observed heads against a previously anchored head file."
    )
    parser.add_argument("--selftest", action="store_true", help="Run the built-in self-test and exit.")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable JSON report.")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    try:
        lines = read_input(args.logfile)
    except OSError as exc:
        print(f"audit-verify: cannot read input: {exc}", file=sys.stderr)
        return 2

    raw_events = extract_audit_events(lines)
    conflicts = divergent_duplicates(raw_events)
    events = deduplicate(raw_events)
    chains = group_into_chains(events)
    results = [verify_chain(chain) for chain in chains]

    anchor_problems: list[str] = []
    if args.anchor:
        try:
            anchored = load_anchor_file(Path(args.anchor))
        except (OSError, ValueError, KeyError) as exc:
            print(f"audit-verify: cannot read anchor file {args.anchor}: {exc}", file=sys.stderr)
            return 2
        anchor_problems = compare_anchor(observed_heads(results), anchored)

    ok = all(result.ok for result in results) and not anchor_problems and not conflicts

    if args.json:
        report = {
            "chains": [
                {
                    "chain_id": r.chain_id,
                    "records": r.count,
                    "ok": r.ok,
                    "head": r.head,
                    "reason": r.reason,
                    "position": r.position,
                }
                for r in results
            ],
            "anchor_problems": anchor_problems,
            "divergent_duplicates": conflicts,
            "ok": ok,
        }
        print(json.dumps(report, indent=2))
    else:
        if not results:
            print("audit-verify: no audit events found in input")
        for r in results:
            if r.ok:
                print(f"chain {r.chain_id}: OK ({r.count} record(s), head {r.head[:16]}...)")
            else:
                print(f"chain {r.chain_id}: BROKEN at position {r.position} ({r.reason}); {r.count} record(s)")
        for problem in anchor_problems:
            print(f"anchor: {problem}")
        for digest in conflicts:
            print(f"tampered: two log copies share record_hash {digest[:16]}... but differ in content")

    if not ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
