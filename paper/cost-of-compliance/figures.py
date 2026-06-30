#!/usr/bin/env python3
"""Generate the paper figures from the measured results.

Reads results/aggregate.json, results/paper-numbers.json, results/micro-results.json
and writes vector PDFs into ../../figures/. Run with the Windows Python that has
matplotlib installed.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
FIGDIR = HERE.parent / "figures"

plt.rcParams.update({
    "font.size": 8,
    "font.family": "serif",
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.linewidth": 0.4,
    "axes.linewidth": 0.6,
    "figure.dpi": 200,
})

GRAY = "#444444"
ACCENT = "#1f4e79"
LIGHT = "#9ab4d0"


def load(name):
    return json.loads((RESULTS / name).read_text())


def fig_micro():
    micro = load("micro-results.json")
    c = micro["controls"]
    items = [
        ("API-key auth", c["auth_api_key"]["ns_per_op"] / 1000.0),
        ("Model routing", c["model_routing_resolve"]["ns_per_op"] / 1000.0),
        ("Budget reserve", c["budget_reserve"]["ns_per_op"] / 1000.0),
        ("Audit (hash+serialise)", c["audit_fingerprint_and_serialize"]["ns_per_op"] / 1000.0),
        ("Admission (+secret scan)", c["admission_with_secret_detection"]["ns_per_op"] / 1000.0),
    ]
    items.sort(key=lambda x: x[1])
    labels = [x[0] for x in items]
    vals = [x[1] for x in items]
    total = micro["full_governance_us_per_request"]

    fig, ax = plt.subplots(figsize=(3.4, 2.1))
    bars = ax.barh(labels, vals, color=ACCENT, height=0.6)
    ax.set_xlabel("CPU time per request ($\\mu$s)")
    ax.bar_label(bars, fmt="%.1f", padding=2, fontsize=7)
    ax.set_xlim(0, max(vals) * 1.25)
    ax.set_title(f"Full governance path: {total:.1f} $\\mu$s/request", fontsize=8)
    ax.margins(y=0.04)
    fig.tight_layout(pad=0.4)
    fig.savefig(FIGDIR / "fig_micro.pdf")
    plt.close(fig)


def fig_overhead():
    nums = load("paper-numbers.json")
    rows = nums["overhead_vs_delay"]
    delays = [r["delay_ms"] for r in rows]
    delta = [r["governance_delta_ms"] for r in rows]
    pct = [r["governance_pct_of_full"] for r in rows]
    xlabels = [("0\n(pure)" if d == 0 else str(d)) for d in delays]
    x = list(range(len(delays)))

    fig, ax1 = plt.subplots(figsize=(3.4, 2.1))
    bars = ax1.bar(x, delta, width=0.55, color=LIGHT, label="absolute overhead")
    ax1.set_ylabel("Governance overhead (ms)", color=GRAY)
    ax1.set_xlabel("Emulated inference think-time (ms)")
    ax1.set_xticks(x)
    ax1.set_xticklabels(xlabels)
    ax1.bar_label(bars, fmt="%.2f", padding=2, fontsize=6.5)

    ax2 = ax1.twinx()
    ax2.plot(x, pct, color=ACCENT, marker="o", markersize=3.5, linewidth=1.2, label="% of end-to-end")
    ax2.set_ylabel("% of end-to-end latency", color=ACCENT)
    ax2.set_ylim(0, max(pct) * 1.3)
    ax2.grid(False)
    for xi, p in zip(x, pct, strict=True):
        ax2.annotate(f"{p:.1f}%", (xi, p), textcoords="offset points", xytext=(0, 5),
                     fontsize=6.5, color=ACCENT, ha="center")
    fig.tight_layout(pad=0.4)
    fig.savefig(FIGDIR / "fig_overhead.pdf")
    plt.close(fig)


def fig_latency_concurrency():
    agg = load("aggregate.json")

    def series(config):
        pts = sorted(
            ((v["concurrency"], v["p50_ms"]["mean"]) for v in agg.values()
             if v["config"] == config and v["delay_ms"] == 0),
            key=lambda t: t[0],
        )
        return [p[0] for p in pts], [p[1] for p in pts]

    fig, ax = plt.subplots(figsize=(3.4, 2.1))
    for config, color, marker, label in [
        ("direct", GRAY, "s", "direct (baseline)"),
        ("gw-min", LIGHT, "^", "gateway, ungoverned"),
        ("gw-full", ACCENT, "o", "gateway, governed"),
    ]:
        xs, ys = series(config)
        if xs:
            ax.plot(xs, ys, color=color, marker=marker, markersize=3.5, linewidth=1.1, label=label)
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xlabel("Concurrency (virtual users)")
    ax.set_ylabel("p50 latency (ms)")
    ax.legend(fontsize=6.5, loc="upper left")
    fig.tight_layout(pad=0.4)
    fig.savefig(FIGDIR / "fig_latency.pdf")
    plt.close(fig)


def main():
    FIGDIR.mkdir(parents=True, exist_ok=True)
    fig_micro()
    fig_latency_concurrency()
    # fig_overhead() is retained as a function but unused in the paper: the
    # governed-minus-ungoverned delay delta is sub-millisecond and at the noise
    # floor, which reads more honestly in text than as a figure.
    print(f"wrote figures to {FIGDIR}")


if __name__ == "__main__":
    main()
