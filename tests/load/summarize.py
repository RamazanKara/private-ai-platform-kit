from __future__ import annotations

import json
import sys
from pathlib import Path


def metric_value(summary: dict, name: str, key: str, default: str = "n/a") -> str:
    try:
        metric = summary["metrics"][name]
    except KeyError:
        return default
    value = metric.get(key)
    if value is None and key == "rate":
        value = metric.get("value")
    if value is None:
        return default
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: summarize.py <k6-summary.json> <summary.md>", file=sys.stderr)
        return 2
    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    summary = json.loads(source.read_text())
    lines = [
        "# Load Test Summary",
        "",
        f"- Runtime backend: {summary.get('root_group', {}).get('name', 'chat')}",
        f"- Requests: {metric_value(summary, 'http_reqs', 'count')}",
        f"- Error rate: {metric_value(summary, 'http_req_failed', 'rate')}",
        f"- p50 latency ms: {metric_value(summary, 'http_req_duration', 'p(50)')}",
        f"- p95 latency ms: {metric_value(summary, 'http_req_duration', 'p(95)')}",
        f"- p99 latency ms: {metric_value(summary, 'http_req_duration', 'p(99)')}",
        f"- Throughput req/s: {metric_value(summary, 'http_reqs', 'rate')}",
        "",
        f"Raw summary: `{source}`",
        "",
    ]
    target.write_text("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
