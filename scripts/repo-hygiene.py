#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")

REQUIRED_FILES = (
    ".editorconfig",
    ".github/CODEOWNERS",
    ".github/workflows/ci.yml",
    ".gitignore",
    "api-contracts/README.md",
    "api-contracts/inference-gateway.openapi.json",
    "api-contracts/rag-service.openapi.json",
    "CHANGELOG.md",
    "config-contracts/README.md",
    "config-contracts/inference-gateway.config.json",
    "config-contracts/rag-service.config.json",
    "CONTRIBUTING.md",
    "LICENSE",
    "Makefile",
    "README.md",
    "SECURITY.md",
    "docs/README.md",
    "docs/getting-started.md",
    "docs/production-readiness.md",
    "runbooks/evidence-pack.md",
    "runbooks/release-gates.md",
    "scripts/api-contract.py",
    "scripts/config-contract.py",
    "scripts/image-scan.sh",
    "scripts/production-check.py",
    "scripts/validate.sh",
)

REQUIRED_DIRECTORIES = (
    "backup",
    "api-contracts",
    "charts",
    "clusters",
    "docs",
    "evals",
    "gitops",
    "governance",
    "model-catalog",
    "network",
    "observability",
    "policies",
    "results",
    "runbooks",
    "sandbox",
    "scripts",
    "services",
    "slo",
    "tenants",
    "tests",
    "tools",
)

REQUIRED_MAKE_TARGETS = (
    "help",
    "validate",
    "validate-full",
    "production-check",
    "repo-hygiene",
    "api-contract",
    "api-contract-update",
    "config-contract",
    "config-contract-update",
    "image-scan",
    "release-gate",
    "release-gate-strict",
    "customer-overlay",
    "tenant-onboard",
)

IGNORED_MARKDOWN_PARTS = {
    ".git",
    ".pytest_cache",
    ".tools",
    ".venv",
    "services",
    "tenants/generated",
}


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def check_required_paths(errors: list[str]) -> None:
    for item in REQUIRED_FILES:
        require(errors, (ROOT / item).is_file(), f"required file missing: {item}")
    for item in REQUIRED_DIRECTORIES:
        require(errors, (ROOT / item).is_dir(), f"required directory missing: {item}")


def check_makefile(errors: list[str]) -> None:
    text = (ROOT / "Makefile").read_text()
    for target in REQUIRED_MAKE_TARGETS:
        require(errors, re.search(rf"^{re.escape(target)}:", text, re.MULTILINE) is not None, f"Makefile missing target: {target}")
    require(errors, ".PHONY:" in text and "repo-hygiene" in text, "Makefile must include repo-hygiene in .PHONY")
    require(errors, "help:" in text and "Private AI Platform Kit targets" in text, "Makefile must expose a useful help target")


def check_script_modes(errors: list[str]) -> None:
    for path in sorted((ROOT / "scripts").iterdir()):
        if path.suffix not in {".py", ".sh"}:
            continue
        require(errors, os.access(path, os.X_OK), f"{rel(path)} must be executable")


def check_runtime_dependencies(errors: list[str]) -> None:
    for service in ("inference-gateway", "rag-service"):
        base = ROOT / "services" / service
        runtime_requirements = base / "requirements.txt"
        dev_requirements = base / "requirements-dev.txt"
        dockerfile = base / "Dockerfile"
        require(errors, runtime_requirements.exists(), f"{rel(runtime_requirements)} missing")
        require(errors, dev_requirements.exists(), f"{rel(dev_requirements)} missing")
        require(errors, dockerfile.exists(), f"{rel(dockerfile)} missing")
        if runtime_requirements.exists():
            runtime_text = runtime_requirements.read_text()
            require(errors, "pytest" not in runtime_text, f"{rel(runtime_requirements)} must not include test-only dependencies")
        if dev_requirements.exists():
            dev_text = dev_requirements.read_text()
            require(errors, "-r requirements.txt" in dev_text, f"{rel(dev_requirements)} must extend runtime requirements")
            require(errors, "pytest" in dev_text, f"{rel(dev_requirements)} must include pytest for local tests")
        if dockerfile.exists():
            dockerfile_text = dockerfile.read_text()
            require(errors, "python:3.14-alpine@sha256:" in dockerfile_text, f"{rel(dockerfile)} must use a pinned Alpine base")
            require(errors, "3.14-slim" not in dockerfile_text, f"{rel(dockerfile)} must not use the Debian slim base")


def markdown_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*.md"):
        parts = set(path.relative_to(ROOT).parts)
        if parts & IGNORED_MARKDOWN_PARTS:
            continue
        files.append(path)
    return sorted(files)


def link_target(raw_target: str) -> str:
    target = raw_target.strip()
    if not target or target.startswith("#"):
        return ""
    if " " in target:
        target = target.split()[0]
    parsed = urlparse(target)
    if parsed.scheme in {"http", "https", "mailto"}:
        return ""
    if target.startswith("mailto:"):
        return ""
    return unquote(target.split("#", 1)[0])


def check_markdown_links(errors: list[str]) -> None:
    for path in markdown_files():
        for match in LINK_PATTERN.finditer(path.read_text()):
            target = link_target(match.group(1))
            if not target:
                continue
            candidate = (path.parent / target).resolve()
            try:
                candidate.relative_to(ROOT)
            except ValueError:
                errors.append(f"{rel(path)} links outside repo: {match.group(1)}")
                continue
            if not candidate.exists():
                errors.append(f"{rel(path)} has broken link: {match.group(1)}")


def run_checks() -> list[str]:
    errors: list[str] = []
    check_required_paths(errors)
    check_makefile(errors)
    check_script_modes(errors)
    check_runtime_dependencies(errors)
    check_markdown_links(errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Check repository hygiene, contributor docs, executable bits, dependencies, and markdown links.")
    parser.add_argument("--check", action="store_true", help="Run checks and exit non-zero on failures.")
    args = parser.parse_args()

    errors = run_checks()
    if errors:
        print("repo hygiene failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("repo hygiene ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
