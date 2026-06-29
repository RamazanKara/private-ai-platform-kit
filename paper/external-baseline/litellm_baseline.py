#!/usr/bin/env python3
"""External-baseline experiment: measure an existing OpenAI-compatible gateway
(LiteLLM proxy) in front of the same mock runtime, so the governed gateway's
overhead can be read against a comparable system rather than only in isolation.

Reports, for the direct path and the LiteLLM proxy path, latency and throughput at
matching concurrency levels. Compare these to the gw-min / gw-full numbers from the
main cost-of-compliance run at the same concurrency.

Usage (after `pip install 'litellm[proxy]'` into LITELLM_PY's environment):
    LITELLM_PY=/path/to/venv/bin/python python litellm_baseline.py
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
COC = HERE.parent / "cost-of-compliance"
sys.path.insert(0, str(COC))
import bench  # noqa: E402

RESULTS = HERE / "results"
PYTHON = sys.executable
LITELLM_BIN = os.environ.get("LITELLM_BIN", "litellm")
MOCK_PORT = int(os.environ.get("MOCK_PORT", "9099"))
LITELLM_PORT = int(os.environ.get("LITELLM_PORT", "8090"))
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"
LITELLM_URL = f"http://127.0.0.1:{LITELLM_PORT}"
CONCURRENCY = [1, 4, 16]
REPEATS = int(os.environ.get("REPEATS", "5"))
WARMUP = float(os.environ.get("WARMUP", "2"))
DURATION = float(os.environ.get("DURATION", "8"))


def wait_ok(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        for path in ("/health/liveliness", "/health/readiness", "/v1/models", "/healthz"):
            try:
                r = httpx.get(f"{url}{path}", timeout=2.0)
                if r.status_code < 500:
                    return
                last = r.status_code
            except Exception as exc:  # noqa: BLE001
                last = type(exc).__name__
        time.sleep(0.5)
    raise RuntimeError(f"{url} not ready (last={last})")


def measure(target_url: str, label: str) -> list[dict]:
    rows = []
    for conc in CONCURRENCY:
        for rep in range(REPEATS):
            m = asyncio.run(
                bench.run_load(f"{target_url}/v1/chat/completions", concurrency=conc, warmup=WARMUP, duration=DURATION)
            )
            m.pop("_latencies", None)
            rows.append({"target": label, "concurrency": conc, "repeat": rep,
                         "p50": m["latency_ms"]["p50"], "p95": m["latency_ms"]["p95"],
                         "throughput_rps": m["throughput_rps"], "errors": m["errors"]})
            print(f"  {label:>8} c={conc:>2} r={rep} p50={m['latency_ms']['p50']:.2f} "
                  f"rps={m['throughput_rps']:.0f} err={m['errors']}", flush=True)
    return rows


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    mock = subprocess.Popen(
        [PYTHON, str(COC / "mock_runtime.py"), "--port", str(MOCK_PORT), "--delay-ms", "0"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    lllog = open(RESULTS / "litellm.log", "w")  # noqa: SIM115
    litellm = subprocess.Popen(
        [LITELLM_BIN, "--config", str(HERE / "litellm_config.yaml"),
         "--host", "127.0.0.1", "--port", str(LITELLM_PORT)],
        stdout=lllog, stderr=lllog,
    )
    rows: list[dict] = []
    try:
        wait_ok(MOCK_URL)
        wait_ok(LITELLM_URL)
        print("=== direct ==="); rows += measure(MOCK_URL, "direct")
        print("=== litellm ==="); rows += measure(LITELLM_URL, "litellm")
    finally:
        for p in (litellm, mock):
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        lllog.close()
    out = {"experiment": "external-baseline", "rows": rows}
    (RESULTS / "litellm-baseline.json").write_text(json.dumps(out, indent=2))
    print(f"\nwrote {RESULTS / 'litellm-baseline.json'} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
