#!/usr/bin/env python3
"""Canonical repository directory layout -- the single source of truth.

Governance gates, shell scripts (via ``scripts/_paths.sh``), and tooling resolve
the repository's top-level directories from here, so relocating a directory is a
one-line change instead of hundreds of scattered path literals.

CLI:
  paths.py --dump      Emit the layout as JSON (root + directory purposes).
  paths.py --dump-sh   Emit ``NAME_DIR="relname"`` assignments for shell consumers.
  paths.py --check     Verify the on-disk layout matches this declaration and
                       flag any undeclared top-level directory (layout drift).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Top-level directory name -> human-readable purpose. Ordered by role so the
# rendered manifest reads as an architecture map (services -> platform ->
# governance inputs -> contracts -> tooling -> docs/evidence).
DIRECTORIES: dict[str, str] = {
    "src": "Python FastAPI services (inference gateway and RAG service).",
    "deploy": "Deployable Kubernetes surface: charts, clusters, gitops, policies, sandbox, backup, observability.",
    "chaos": "Chaos and resilience drill definitions.",
    "platform": "Governance and contract inputs: governance, network, slo, model-catalog, evals, rag, api-contracts, config-contracts, tools.",
    "tenants": "Tenant onboarding specs and generated artifacts.",
    "loadtest": "k6 load tests and the mock runtime.",
    "scripts": "Automation, governance gates, and tooling.",
    "runbooks": "Operational runbooks (also shipped as Prometheus runbook_url targets).",
    "docs": "User-facing documentation and the mkdocs site source.",
    "results": "Sample evidence (tracked) and generated reports (git-ignored except sample-*).",
}

# Rendered tenant artifacts go to the git-ignored .out/tenants tree; .out is a
# dotdir, so it is excluded from the layout inventory and the drift guard below.

# Build-time / tooling directories that are intentionally outside the layout and
# must not be flagged as undeclared by --check (mkdocs output, etc.).
NON_INVENTORY = frozenset({"site"})

# Path objects for programmatic use: PATHS["charts"] -> ROOT/charts.
PATHS: dict[str, Path] = {name: ROOT / name for name in DIRECTORIES}


def relative(name: str) -> str:
    """Return the repo-relative directory name, validating it is declared."""
    if name not in DIRECTORIES:
        raise KeyError(f"{name!r} is not a declared top-level directory")
    return name


def path(name: str) -> Path:
    """Return the absolute Path for a declared top-level directory."""
    return ROOT / relative(name)


def required_directories() -> tuple[str, ...]:
    """Directory names every checkout must contain, in declaration order."""
    return tuple(DIRECTORIES)


def shell_var(name: str) -> str:
    """Shell variable name for a directory (config-contracts -> CONFIG_CONTRACTS_DIR)."""
    return f"{name.upper().replace('-', '_')}_DIR"


def discovered_top_level() -> set[str]:
    """Tracked top-level directories on disk, excluding dotdirs and build output."""
    found: set[str] = set()
    for child in ROOT.iterdir():
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name in NON_INVENTORY:
            continue
        found.add(child.name)
    return found


def check() -> list[str]:
    """Return layout problems: declared-but-missing and on-disk-but-undeclared dirs."""
    errors: list[str] = []
    for name in DIRECTORIES:
        if not (ROOT / name).is_dir():
            errors.append(f"declared directory missing on disk: {name}/")
    for name in sorted(discovered_top_level() - set(DIRECTORIES)):
        errors.append(f"undeclared top-level directory (add it to scripts/paths.py): {name}/")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Canonical repository directory layout.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dump", action="store_true", help="emit the layout as JSON")
    group.add_argument("--dump-sh", action="store_true", help='emit NAME_DIR="relname" shell assignments')
    group.add_argument("--check", action="store_true", help="verify the on-disk layout matches this declaration")
    args = parser.parse_args(argv)

    if args.dump_sh:
        for name in DIRECTORIES:
            print(f'{shell_var(name)}="{name}"')
        return 0

    if args.check:
        errors = check()
        if errors:
            print("layout check failed:")
            for err in errors:
                print(f"- {err}")
            return 1
        print(f"layout ok: {len(DIRECTORIES)} declared directories")
        return 0

    print(json.dumps({"root": str(ROOT), "directories": DIRECTORIES}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
