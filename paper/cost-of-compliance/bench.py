#!/usr/bin/env python3
"""Closed-loop load generator for the cost-of-compliance experiment.

A fixed number of concurrent virtual users each issue chat-completion requests
back to back for a measurement window, after a warmup window whose samples are
discarded. Reports the latency distribution and sustained throughput as JSON.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
import statistics
import time
from typing import Any

import httpx

# Disable Nagle on the client sockets. On the loopback, Nagle on the sender plus
# delayed ACK on the receiver adds a ~40 ms stall per request that is unrelated to
# the gateway; clearing it keeps the measured latency attributable to real work.
_SOCKET_OPTIONS = [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]

DEFAULT_PROMPT = (
    "You are a platform assistant. Summarise, in two sentences, why a regulated "
    "organisation might run language models on its own Kubernetes cluster instead "
    "of calling a hosted commercial API, and what evidence an auditor would expect."
)


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile on a sorted copy; returns 0.0 for an empty input."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if pct <= 0:
        return ordered[0]
    if pct >= 100:
        return ordered[-1]
    rank = max(1, round(pct / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


async def _worker(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    stop_at: float,
    records: list[tuple[float, float, bool]],
) -> None:
    while time.perf_counter() < stop_at:
        start = time.perf_counter()
        ok = True
        try:
            response = await client.post(url, json=payload, headers=headers)
            ok = response.status_code == 200
            _ = response.content
        except Exception:
            ok = False
        end = time.perf_counter()
        records.append((end, (end - start) * 1000.0, ok))


async def run_load(
    url: str,
    concurrency: int,
    warmup: float,
    duration: float,
    headers: dict[str, str] | None = None,
    prompt: str = DEFAULT_PROMPT,
    model: str = "bench-model",
    sandbox_id: str = "bench",
) -> dict[str, Any]:
    """Drive `concurrency` virtual users against `url` and return latency/throughput metrics."""
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    base_headers = {
        "Content-Type": "application/json",
        "X-Sandbox-ID": sandbox_id,
        "X-Request-ID": "coc-bench",
    }
    if headers:
        base_headers.update(headers)

    limits = httpx.Limits(
        max_connections=concurrency + 16,
        max_keepalive_connections=concurrency + 16,
    )
    timeout = httpx.Timeout(30.0)
    transport = httpx.AsyncHTTPTransport(limits=limits, socket_options=_SOCKET_OPTIONS)
    records: list[tuple[float, float, bool]] = []
    async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
        start = time.perf_counter()
        warmup_until = start + warmup
        stop_at = start + warmup + duration
        tasks = [
            asyncio.create_task(_worker(client, url, payload, base_headers, stop_at, records))
            for _ in range(concurrency)
        ]
        await asyncio.gather(*tasks)

    window = [(lat, ok) for (ts, lat, ok) in records if warmup_until <= ts <= stop_at]
    latencies = [lat for (lat, ok) in window if ok]
    errors = sum(1 for (_, ok) in window if not ok)
    total = len(window)
    return {
        "url": url,
        "concurrency": concurrency,
        "warmup_s": warmup,
        "duration_s": duration,
        "requests": total,
        "errors": errors,
        "error_rate": (errors / total) if total else 0.0,
        "throughput_rps": (len(latencies) / duration) if duration else 0.0,
        "_latencies": latencies,
        "latency_ms": {
            "p50": round(_percentile(latencies, 50), 3),
            "p90": round(_percentile(latencies, 90), 3),
            "p95": round(_percentile(latencies, 95), 3),
            "p99": round(_percentile(latencies, 99), 3),
            "mean": round(statistics.fmean(latencies), 3) if latencies else 0.0,
            "stdev": round(statistics.pstdev(latencies), 3) if len(latencies) > 1 else 0.0,
            "min": round(min(latencies), 3) if latencies else 0.0,
            "max": round(max(latencies), 3) if latencies else 0.0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Closed-loop load generator.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--warmup", type=float, default=2.0)
    parser.add_argument("--duration", type=float, default=8.0)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-key-header", default="X-API-Key")
    parser.add_argument("--raw", action="store_true", help="include the raw latency list in the JSON output")
    args = parser.parse_args()

    headers = {}
    if args.api_key:
        headers[args.api_key_header] = args.api_key
    result = asyncio.run(
        run_load(
            args.url,
            concurrency=args.concurrency,
            warmup=args.warmup,
            duration=args.duration,
            headers=headers,
        )
    )
    if not args.raw:
        result.pop("_latencies", None)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
