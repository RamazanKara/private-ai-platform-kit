#!/usr/bin/env python3
"""Validate Grafana dashboards: JSON structure and that referenced platform metrics exist.

The metric cross-check keeps dashboards honest: every ``inference_gateway_*`` or
``rag_service_*`` series used in a panel query must be a metric the service actually emits,
so renaming or removing a metric without updating its dashboard fails validation.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = ROOT / "observability/dashboards"
REQUIRED_DASHBOARDS = (
    "inference-dashboard.json",
    "rag-dashboard.json",
    "restore-drill-dashboard.json",
)
METRIC_PATTERN = re.compile(r"(?:inference_gateway|rag_service)_[a-z0-9_]+")
HISTOGRAM_SUFFIXES = ("_bucket", "_sum", "_count")
SERVICE_SOURCES = {
    "inference_gateway": ROOT / "services/inference-gateway/app",
    "rag_service": ROOT / "services/rag-service/app",
}


def require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def service_metric_names(prefix: str) -> set[str]:
    names: set[str] = set()
    for path in SERVICE_SOURCES[prefix].glob("*.py"):
        names.update(re.findall(rf"{prefix}_[a-z0-9_]+", path.read_text(encoding="utf-8")))
    return names


def base_metric_name(metric: str) -> str:
    for suffix in HISTOGRAM_SUFFIXES:
        if metric.endswith(suffix):
            return metric[: -len(suffix)]
    return metric


def collect_exprs(panels: list[dict]) -> list[str]:
    return [
        target["expr"]
        for panel in panels
        for target in panel.get("targets", [])
        if isinstance(target, dict) and target.get("expr")
    ]


def check_dashboards() -> list[str]:
    errors: list[str] = []
    require(errors, DASHBOARD_DIR.is_dir(), "observability/dashboards directory must exist")
    for name in REQUIRED_DASHBOARDS:
        require(errors, (DASHBOARD_DIR / name).is_file(), f"required dashboard missing: {name}")

    known_metrics = service_metric_names("inference_gateway") | service_metric_names("rag_service")

    for path in sorted(DASHBOARD_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}: invalid JSON: {exc}")
            continue
        require(errors, isinstance(data, dict), f"{path.name}: dashboard must be a JSON object")
        if not isinstance(data, dict):
            continue
        require(errors, bool(data.get("title")), f"{path.name}: dashboard must set a title")
        require(errors, bool(data.get("uid")), f"{path.name}: dashboard must set a uid")
        panels = data.get("panels")
        require(errors, isinstance(panels, list) and bool(panels), f"{path.name}: dashboard must define panels")
        if not isinstance(panels, list):
            continue
        exprs = collect_exprs(panels)
        require(errors, bool(exprs), f"{path.name}: dashboard must define at least one panel query")
        for expr in exprs:
            for metric in METRIC_PATTERN.findall(expr):
                base = base_metric_name(metric)
                require(errors, base in known_metrics, f"{path.name}: query references unknown platform metric '{base}'")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Grafana dashboard structure and metric references.")
    parser.add_argument("--check", action="store_true", help="Run checks and exit non-zero on failures.")
    parser.parse_args()
    errors = check_dashboards()
    if errors:
        print("dashboard check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"dashboards ok ({len(REQUIRED_DASHBOARDS)} required dashboards validated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
