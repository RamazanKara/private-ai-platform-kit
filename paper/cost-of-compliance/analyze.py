#!/usr/bin/env python3
"""Aggregate the cost-of-compliance raw results into per-cell statistics and the
scalar numbers the paper cites. Reads raw-results.json (+ micro-results.json) and
writes aggregate.json and paper-numbers.json; prints a readable summary.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def load(name: str) -> dict:
    path = RESULTS / name
    return json.loads(path.read_text()) if path.exists() else {}


def agg(values: list[float]) -> dict:
    return {
        "mean": round(statistics.fmean(values), 3) if values else 0.0,
        "stdev": round(statistics.pstdev(values), 3) if len(values) > 1 else 0.0,
        "min": round(min(values), 3) if values else 0.0,
        "max": round(max(values), 3) if values else 0.0,
        "n": len(values),
    }


def main() -> int:
    raw = load("raw-results.json")
    runs = list(raw.get("runs", []))
    # The corrected delay sweep (PHASE=delay) supersedes any overlapping cells in the
    # main run for delay-dependent measurements.
    delay = load("delay-results.json")
    if delay.get("runs"):
        override = {(r["config"], r["delay_ms"], r["concurrency"]) for r in delay["runs"]}
        runs = [r for r in runs if (r["config"], r["delay_ms"], r["concurrency"]) not in override]
        runs += delay["runs"]
    cells: dict[tuple, list[dict]] = defaultdict(list)
    for r in runs:
        cells[(r["config"], r["delay_ms"], r["concurrency"])].append(r)

    aggregate = {}
    for (config, delay, conc), rows in sorted(cells.items()):
        aggregate[f"{config}|d{delay}|c{conc}"] = {
            "config": config, "delay_ms": delay, "concurrency": conc,
            "p50_ms": agg([x["latency_ms"]["p50"] for x in rows]),
            "p95_ms": agg([x["latency_ms"]["p95"] for x in rows]),
            "p99_ms": agg([x["latency_ms"]["p99"] for x in rows]),
            "throughput_rps": agg([x["throughput_rps"] for x in rows]),
            "errors": sum(x["errors"] for x in rows),
        }
    (RESULTS / "aggregate.json").write_text(json.dumps(aggregate, indent=2))

    def cell(config, delay, conc):
        return aggregate.get(f"{config}|d{delay}|c{conc}")

    micro = load("micro-results.json")
    numbers: dict = {"system": raw.get("system", {}), "params": raw.get("params", {})}

    # Governance latency delta at low concurrency (clean signal, c=1).
    gmin1, gfull1 = cell("gw-min", 0, 1), cell("gw-full", 0, 1)
    direct1 = cell("direct", 0, 1)
    if gmin1 and gfull1:
        numbers["c1"] = {
            "direct_p50": direct1["p50_ms"]["mean"] if direct1 else None,
            "gw_min_p50": gmin1["p50_ms"]["mean"],
            "gw_full_p50": gfull1["p50_ms"]["mean"],
            "governance_delta_p50_ms": round(gfull1["p50_ms"]["mean"] - gmin1["p50_ms"]["mean"], 3),
            "gw_min_p95": gmin1["p95_ms"]["mean"],
            "gw_full_p95": gfull1["p95_ms"]["mean"],
            "governance_delta_p95_ms": round(gfull1["p95_ms"]["mean"] - gmin1["p95_ms"]["mean"], 3),
            "gw_min_rps": gmin1["throughput_rps"]["mean"],
            "gw_full_rps": gfull1["throughput_rps"]["mean"],
        }

    # Throughput ceiling (max over concurrency) per config at delay 0.
    def max_rps(config):
        vals = [v["throughput_rps"]["mean"] for k, v in aggregate.items()
                if v["config"] == config and v["delay_ms"] == 0]
        return round(max(vals), 1) if vals else None
    numbers["max_rps"] = {c: max_rps(c) for c in ("direct", "gw-min", "gw-full")}

    # Governance overhead as a fraction of end-to-end latency across mock think-time.
    # The delay sweep runs at concurrency 1 so neither side is gateway-throughput-bound;
    # governance is then a fixed latency add whose fraction shrinks as inference grows.
    ctx_conc = 1
    for key in aggregate:
        if "|d50|" in key and key.startswith("gw-min|"):
            ctx_conc = aggregate[key]["concurrency"]
            break
    overhead_vs_delay = []
    for delay in (0, 50, 200, 500):
        gm, gf = cell("gw-min", delay, ctx_conc), cell("gw-full", delay, ctx_conc)
        if gm and gf:
            delta = gf["p50_ms"]["mean"] - gm["p50_ms"]["mean"]
            overhead_vs_delay.append({
                "delay_ms": delay,
                "gw_min_p50": gm["p50_ms"]["mean"],
                "gw_full_p50": gf["p50_ms"]["mean"],
                "governance_delta_ms": round(delta, 3),
                "governance_pct_of_full": round(100.0 * delta / gf["p50_ms"]["mean"], 3) if gf["p50_ms"]["mean"] else None,
            })
    numbers["overhead_vs_delay"] = overhead_vs_delay

    # Per-control microbenchmark (ns) and full governance cost.
    if micro:
        numbers["micro_us"] = {
            k: round(v["ns_per_op"] / 1000.0, 3) for k, v in micro.get("controls", {}).items()
        }
        numbers["full_governance_us"] = micro.get("full_governance_us_per_request")

    (RESULTS / "paper-numbers.json").write_text(json.dumps(numbers, indent=2))

    # Readable summary.
    print("=== max sustained throughput (rps, delay 0) ===")
    for c, v in numbers["max_rps"].items():
        print(f"  {c:<10} {v}")
    if "c1" in numbers:
        c1 = numbers["c1"]
        print("\n=== concurrency 1 (clean per-request) ===")
        print(f"  direct p50      {c1['direct_p50']} ms")
        print(f"  gw-min p50      {c1['gw_min_p50']} ms   gw-full p50 {c1['gw_full_p50']} ms")
        print(f"  governance dp50 {c1['governance_delta_p50_ms']} ms   dp95 {c1['governance_delta_p95_ms']} ms")
    print("\n=== governance overhead vs inference think-time (c=8) ===")
    for row in numbers["overhead_vs_delay"]:
        print(f"  delay {row['delay_ms']:>3}ms  gov-delta {row['governance_delta_ms']:>7.3f} ms  "
              f"= {row['governance_pct_of_full']}% of end-to-end")
    if micro:
        print(f"\n=== microbench: full governance {numbers['full_governance_us']} us/request ===")
        for k, v in numbers["micro_us"].items():
            print(f"  {k:<38} {v} us")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
