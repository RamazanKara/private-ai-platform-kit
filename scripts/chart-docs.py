#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHARTS_DIR = ROOT / "deploy/charts"
START = "<!-- chart-docs:start -->"
END = "<!-- chart-docs:end -->"


def chart_dirs() -> list[Path]:
    return sorted(path for path in CHARTS_DIR.iterdir() if (path / "Chart.yaml").exists())


def flatten(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        rows: list[tuple[str, Any]] = []
        for key in sorted(value):
            child = value[key]
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, dict) and child:
                rows.extend(flatten(child, child_prefix))
            else:
                rows.append((child_prefix, child))
        return rows
    return [(prefix, value)]


def markdown_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def format_default(value: Any) -> str:
    if value is None:
        rendered = "null"
    elif isinstance(value, str):
        rendered = value if value else '""'
    elif isinstance(value, (int, float, bool)):
        rendered = json.dumps(value)
    else:
        rendered = json.dumps(value, sort_keys=True)
    if len(rendered) > 120:
        rendered = rendered[:117] + "..."
    return f"`{markdown_escape(rendered)}`"


def values_table(chart_dir: Path) -> str:
    values = yaml.safe_load((chart_dir / "values.yaml").read_text(encoding="utf-8")) or {}
    rows = flatten(values)
    lines = [
        START,
        "## Values",
        "",
        "| Value | Default |",
        "| --- | --- |",
    ]
    for key, default in rows:
        lines.append(f"| `{markdown_escape(key)}` | {format_default(default)} |")
    lines.extend([END, ""])
    return "\n".join(lines)


def default_readme(chart_dir: Path) -> str:
    chart = yaml.safe_load((chart_dir / "Chart.yaml").read_text(encoding="utf-8")) or {}
    name = str(chart.get("name") or chart_dir.name)
    description = str(chart.get("description") or "Private AI Platform Kit Helm chart.")
    title = " ".join(part.capitalize() for part in name.replace("-", " ").split())
    return f"# {title} Chart\n\n{description}\n\n"


def render_readme(chart_dir: Path) -> str:
    path = chart_dir / "README.md"
    current = path.read_text(encoding="utf-8") if path.exists() else default_readme(chart_dir)
    generated = values_table(chart_dir)
    if START in current and END in current:
        before = current.split(START, 1)[0].rstrip()
        after = current.split(END, 1)[1].lstrip()
        rendered = f"{before}\n\n{generated}{after}"
    else:
        rendered = current.rstrip() + "\n\n" + generated
    return rendered if rendered.endswith("\n") else rendered + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Helm chart README value tables.")
    parser.add_argument("--check", action="store_true", help="Fail if generated README content is stale.")
    parser.add_argument("--write", action="store_true", help="Write README content.")
    args = parser.parse_args()
    if not args.check and not args.write:
        args.check = True

    stale: list[str] = []
    wrote: list[str] = []
    for chart_dir in chart_dirs():
        readme = chart_dir / "README.md"
        rendered = render_readme(chart_dir)
        current = readme.read_text(encoding="utf-8") if readme.exists() else ""
        if args.write:
            readme.write_text(rendered, encoding="utf-8")
            wrote.append(readme.relative_to(ROOT).as_posix())
        elif current != rendered:
            stale.append(readme.relative_to(ROOT).as_posix())

    if stale:
        print("chart docs are stale; run scripts/chart-docs.py --write")
        for path in stale:
            print(f"- {path}")
        return 1
    if wrote:
        print("wrote chart docs:")
        for path in wrote:
            print(f"- {path}")
    else:
        print("chart docs ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
