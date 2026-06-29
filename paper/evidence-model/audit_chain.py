#!/usr/bin/env python3
"""Tamper-evident audit chain, auditor query, and verifier for gateway audit events.

The inference gateway emits one redacted, fingerprinted audit event per request. This
module layers a hash chain over that event stream so that any modification, insertion,
deletion, or reordering of records is detectable, and provides the auditor query
(by request id, by time window) that an auditor would actually run.

Chain construction (linear, tamper-evident log; Crosby & Wallach, Merkle):
    h_0      = SHA-256("genesis")
    h_i      = SHA-256(h_{i-1} || canonical(record_i))

Detection model: anyone can recompute the chain, so detection of a wholesale rewrite
requires an external commitment to the head hash (anchor). Editing a record without
re-chaining is detected by the internal consistency check; re-chaining is detected by
the anchor mismatch. Both are demonstrated below.

Outputs results/audit-chain-evidence.json.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path
from typing import Any

GENESIS = hashlib.sha256(b"genesis").hexdigest()
HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def canonical(record: dict[str, Any]) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")


def record_hash(prev_hash: str, record: dict[str, Any]) -> str:
    return hashlib.sha256(prev_hash.encode("ascii") + canonical(record)).hexdigest()


def chain(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = []
    prev = GENESIS
    for seq, record in enumerate(records):
        h = record_hash(prev, record)
        entries.append({"seq": seq, "prev_hash": prev, "record": record, "record_hash": h})
        prev = h
    return entries


def head(entries: list[dict[str, Any]]) -> str:
    return entries[-1]["record_hash"] if entries else GENESIS


def verify(entries: list[dict[str, Any]], expected_head: str | None = None) -> dict[str, Any]:
    """Recompute the chain and report the first divergence, plus the anchor check."""
    prev = GENESIS
    for entry in entries:
        if entry["prev_hash"] != prev:
            return {"ok": False, "reason": "broken_link", "seq": entry["seq"]}
        recomputed = record_hash(prev, entry["record"])
        if recomputed != entry["record_hash"]:
            return {"ok": False, "reason": "record_hash_mismatch", "seq": entry["seq"]}
        prev = entry["record_hash"]
    if expected_head is not None and prev != expected_head:
        return {"ok": False, "reason": "anchor_mismatch", "seq": len(entries) - 1}
    return {"ok": True, "records": len(entries), "head": prev}


def query_by_request_id(entries: list[dict[str, Any]], request_id: str) -> list[dict[str, Any]]:
    return [e for e in entries if e["record"].get("request_id") == request_id]


def query_by_window(entries: list[dict[str, Any]], start_ts: int, end_ts: int) -> list[dict[str, Any]]:
    return [e for e in entries if start_ts <= e["record"].get("ts", -1) <= end_ts]


def synthetic_records(n: int = 200) -> list[dict[str, Any]]:
    """Realistic redacted records with unique ids and timestamps for the query demo."""
    base_ts = 1_700_000_000
    models = ["qwen3.5:0.8b", "Qwen/Qwen3-Coder-Next"]
    tenants = ["tenant-a", "tenant-b", "coding-agents"]
    records = []
    for i in range(n):
        content = f"request number {i} from the platform smoke workload"
        records.append(
            {
                "ts": base_ts + i * 7,
                "request_id": f"req-{i:05d}",
                "sandbox_id": tenants[i % len(tenants)],
                "backend": "vllm",
                "model": models[i % len(models)],
                "status_code": 200,
                "latency_ms": round(380 + (i % 13) * 11.5, 1),
                "message_count": 1 + (i % 3),
                "prompt_chars": len(content),
                "prompt_sha256": hashlib.sha256(content.encode()).hexdigest(),
            }
        )
    return records


def ingest_gateway_log(path: Path) -> list[dict[str, Any]]:
    """Parse real gateway audit lines (logging prefix + JSON) into records."""
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for line in path.read_text(errors="ignore").splitlines():
        brace = line.find("{")
        if brace < 0:
            continue
        try:
            obj = json.loads(line[brace:])
        except ValueError:
            continue
        if obj.get("event") == "inference_request":
            records.append(obj)
    return records


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {"experiment": "audit-chain"}

    # 1. Synthetic, labelled records: chain, verify, auditor queries, tamper test.
    records = synthetic_records(200)
    entries = chain(records)
    anchor = head(entries)
    clean = verify(entries, expected_head=anchor)

    by_id = query_by_request_id(entries, "req-00042")
    window = query_by_window(entries, 1_700_000_000 + 50 * 7, 1_700_000_000 + 59 * 7)
    window_verify = verify_slice(entries, window)

    # Naive tamper: edit a stored record's field, leave its hash. Internal check catches it.
    tampered = [dict(e, record=dict(e["record"])) for e in entries]
    tampered[88]["record"]["latency_ms"] = 0.1
    naive = verify(tampered, expected_head=anchor)

    # Sophisticated tamper: re-chain from the edited record so stored hashes are
    # self-consistent. Internal check passes; the external anchor still diverges.
    rechained = chain([dict(e["record"]) for e in tampered])
    rechained_internal = verify(rechained)  # self-consistent, ok=True
    rechained_anchor = verify(rechained, expected_head=anchor)  # anchor mismatch

    out["synthetic"] = {
        "records": len(records),
        "head": anchor,
        "clean_verify": clean,
        "query_by_request_id": {"request_id": "req-00042", "found": len(by_id),
                                 "seq": by_id[0]["seq"] if by_id else None},
        "query_by_window": {"matched": len(window), "slice_verify": window_verify},
        "tamper_naive": {"edited_seq": 88, "detected": not naive["ok"],
                          "reason": naive.get("reason"), "detected_seq": naive.get("seq")},
        "tamper_rechained": {"internal_ok": rechained_internal["ok"],
                              "anchor_detected": not rechained_anchor["ok"],
                              "reason": rechained_anchor.get("reason")},
    }

    # 2. Real gateway audit log, if available: chain, verify, tamper one record.
    gw_log = HERE.parent / "cost-of-compliance" / "results" / "gateway-gw-full-d0.log"
    real = ingest_gateway_log(gw_log)
    if real:
        real_entries = chain(real)
        real_anchor = head(real_entries)
        real_clean = verify(real_entries, expected_head=real_anchor)
        real_tampered = [dict(e, record=dict(e["record"])) for e in real_entries]
        mid = len(real_tampered) // 2
        real_tampered[mid]["record"]["status_code"] = 500
        real_detect = verify(real_tampered, expected_head=real_anchor)
        out["real_gateway_log"] = {
            "source": str(gw_log.name),
            "records": len(real),
            "clean_verify_ok": real_clean["ok"],
            "head": real_anchor,
            "tamper_edited_seq": mid,
            "tamper_detected": not real_detect["ok"],
            "tamper_detected_seq": real_detect.get("seq"),
            "tamper_reason": real_detect.get("reason"),
        }

    (RESULTS / "audit-chain-evidence.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 0


def verify_slice(entries: list[dict[str, Any]], slice_entries: list[dict[str, Any]]) -> bool:
    """Re-verify that a returned window is an untampered contiguous sub-chain."""
    if not slice_entries:
        return True
    for a, b in itertools.pairwise(slice_entries):
        if b["prev_hash"] != a["record_hash"]:
            return False
        if record_hash(a["record_hash"], b["record"]) != b["record_hash"]:
            return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
