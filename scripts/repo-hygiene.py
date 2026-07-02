#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
PIN_PATTERN = re.compile(r"^\s*([A-Za-z0-9_.-]+)==([^\s\\]+)")
MAKE_TARGET_PATTERN = re.compile(r"^([A-Za-z0-9_-]+):", re.MULTILINE)
MAKE_INVOCATION_PATTERN = re.compile(r"\bmake[ \t]+([a-z0-9][a-z0-9_-]*)")
INLINE_CODE_PATTERN = re.compile(r"`([^`]+)`")

REQUIRED_FILES = (
    ".editorconfig",
    ".github/CODEOWNERS",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/ISSUE_TEMPLATE/question.yml",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/workflows/ci.yml",
    ".github/workflows/scorecard.yml",
    ".gitignore",
    "ADOPTERS.md",
    "platform/api-contracts/README.md",
    "platform/api-contracts/inference-gateway.openapi.json",
    "platform/api-contracts/rag-service.openapi.json",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "platform/config-contracts/README.md",
    "platform/config-contracts/inference-gateway.config.json",
    "platform/config-contracts/rag-service.config.json",
    "CONTRIBUTING.md",
    "GOVERNANCE.md",
    "LICENSE",
    "MAINTAINERS.md",
    "Makefile",
    "README.md",
    "ROADMAP.md",
    "SECURITY.md",
    "deploy/charts/README.md",
    "docs/README.md",
    "docs/benchmarks-and-evals.md",
    "docs/customer-handoff-example.md",
    "docs/decision-guide.md",
    "docs/getting-started.md",
    "docs/proof.md",
    "docs/production-readiness.md",
    "docs/quickstart.md",
    "docs/threat-model.md",
    "runbooks/evidence-pack.md",
    "runbooks/release-gates.md",
    "scripts/api-contract.py",
    "scripts/config-contract.py",
    "scripts/eval-local.sh",
    "scripts/image-scan.sh",
    "scripts/production-check.py",
    "scripts/quickstart.sh",
    "scripts/validate.sh",
)

# The canonical top-level directory inventory lives in scripts/paths.py (the single
# source of truth for the repo layout). check_required_paths() verifies it via
# paths.check(), which covers existence and flags any undeclared top-level directory.

REQUIRED_MAKE_TARGETS = (
    "help",
    "clean",
    "clean-all",
    "quickstart",
    "validate",
    "validate-full",
    "production-check",
    "repo-hygiene",
    "api-contract",
    "api-contract-update",
    "config-contract",
    "config-contract-update",
    "image-scan",
    "supply-chain-check",
    "repo-security-scan",
    "dependency-lock-check",
    "release-gate",
    "release-gate-strict",
    "eval-local",
    "customer-overlay",
    "loadtest-local",
    "tenant-onboard",
)

IGNORED_MARKDOWN_PARTS = {
    ".git",
    ".pytest_cache",
    ".tools",
    ".venv",
    "src",
    ".out",
}


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def check_required_paths(errors: list[str]) -> None:
    from paths import check as check_layout

    for item in REQUIRED_FILES:
        require(errors, (ROOT / item).is_file(), f"required file missing: {item}")
    # Directory inventory and layout-drift guard come from the canonical registry.
    errors.extend(check_layout())


def check_makefile(errors: list[str]) -> None:
    text = (ROOT / "Makefile").read_text()
    for target in REQUIRED_MAKE_TARGETS:
        require(errors, re.search(rf"^{re.escape(target)}:", text, re.MULTILINE) is not None, f"Makefile missing target: {target}")
    require(errors, ".PHONY:" in text and "repo-hygiene" in text, "Makefile must include repo-hygiene in .PHONY")
    require(errors, "help:" in text and "Private AI Platform Kit targets" in text, "Makefile must expose a useful help target")
    require(errors, "PYTHONDONTWRITEBYTECODE ?= 1" in text, "Makefile must default PYTHONDONTWRITEBYTECODE=1")
    require(errors, "export PYTHONDONTWRITEBYTECODE" in text, "Makefile must export PYTHONDONTWRITEBYTECODE")
    require(errors, "TOOLCHAIN_BIN_DIR ?= $(CURDIR)/.tools/bin" in text, "Makefile must define TOOLCHAIN_BIN_DIR")
    require(errors, "export PATH := $(TOOLCHAIN_BIN_DIR):$(PATH)" in text, "Makefile must prepend TOOLCHAIN_BIN_DIR to PATH")


def check_script_modes(errors: list[str]) -> None:
    tracked_modes = tracked_file_modes(errors)
    for path in sorted((ROOT / "scripts").iterdir()):
        if path.suffix not in {".py", ".sh"}:
            continue
        relative = rel(path)
        require(errors, os.access(path, os.X_OK), f"{relative} must be executable on disk")
        mode = tracked_modes.get(relative)
        if mode is not None:
            require(errors, mode == "100755", f"{relative} must be tracked with executable mode 100755")


def check_python_bytecode_policy(errors: list[str]) -> None:
    shell_exports = (
        "scripts/common.sh",
        "scripts/bootstrap-python.sh",
        "scripts/test-gateway.sh",
        "scripts/test-rag.sh",
    )
    for item in shell_exports:
        text = (ROOT / item).read_text()
        require(
            errors,
            'PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"' in text,
            f"{item} must suppress Python bytecode writes",
        )
    common = (ROOT / "scripts/common.sh").read_text()
    require(errors, 'TOOLCHAIN_BIN_DIR="${TOOLCHAIN_BIN_DIR:-$(repo_root)/.tools/bin}"' in common, "scripts/common.sh must default TOOLCHAIN_BIN_DIR")
    require(errors, 'export PATH="$TOOLCHAIN_BIN_DIR:$PATH"' in common, "scripts/common.sh must prepend TOOLCHAIN_BIN_DIR to PATH")


def check_toolchain_lookup_policy(errors: list[str]) -> None:
    text = (ROOT / "scripts/toolchain-doctor.py").read_text()
    require(errors, "def toolchain_bin_dirs()" in text, "toolchain-doctor must define managed tool directories")
    managed_index = text.find("for candidate in [directory / command for directory in toolchain_bin_dirs()]:")
    fallback_index = text.find("return shutil.which(command) or \"\"")
    require(errors, managed_index >= 0, "toolchain-doctor must check managed tool directories")
    require(errors, fallback_index >= 0, "toolchain-doctor must fall back to PATH")
    if managed_index >= 0 and fallback_index >= 0:
        require(errors, managed_index < fallback_index, "toolchain-doctor must prefer managed tools before PATH")


def normalized_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-r ")):
            continue
        match = PIN_PATTERN.match(stripped)
        if match:
            pins[normalized_package_name(match.group(1))] = match.group(2)
    return pins


def require_lock_contains_pins(errors: list[str], requirements: Path, lockfile: Path, expected: dict[str, str]) -> None:
    if not lockfile.exists():
        return
    lock_text = lockfile.read_text()
    for name, version in expected.items():
        require(errors, f"{name}=={version}" in lock_text.lower(), f"{rel(lockfile)} must include pinned dependency {name}=={version} from {rel(requirements)}")


def tracked_file_modes(errors: list[str]) -> dict[str, str]:
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--stage", "--", "scripts"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        errors.append(f"failed to inspect tracked script modes with git: {exc}")
        return {}
    modes: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) == 4:
            modes[parts[3]] = parts[0]
    return modes


def check_runtime_dependencies(errors: list[str]) -> None:
    for service in ("inference-gateway", "rag-service"):
        base = ROOT / "src" / service
        runtime_requirements = base / "requirements.txt"
        dev_requirements = base / "requirements-dev.txt"
        runtime_lock = base / "requirements.lock"
        dev_lock = base / "requirements-dev.lock"
        dockerfile = base / "Dockerfile"
        require(errors, runtime_requirements.exists(), f"{rel(runtime_requirements)} missing")
        require(errors, dev_requirements.exists(), f"{rel(dev_requirements)} missing")
        require(errors, runtime_lock.exists(), f"{rel(runtime_lock)} missing")
        require(errors, dev_lock.exists(), f"{rel(dev_lock)} missing")
        require(errors, dockerfile.exists(), f"{rel(dockerfile)} missing")
        runtime_pins: dict[str, str] = {}
        if runtime_requirements.exists():
            runtime_text = runtime_requirements.read_text()
            runtime_pins = requirement_pins(runtime_requirements)
            require(errors, "pytest" not in runtime_text, f"{rel(runtime_requirements)} must not include test-only dependencies")
            require(errors, runtime_pins, f"{rel(runtime_requirements)} must pin runtime dependencies with == versions")
        if dev_requirements.exists():
            dev_text = dev_requirements.read_text()
            dev_pins = requirement_pins(dev_requirements)
            require(errors, "-r requirements.txt" in dev_text, f"{rel(dev_requirements)} must extend runtime requirements")
            require(errors, "pytest" in dev_text, f"{rel(dev_requirements)} must include pytest for local tests")
            require(errors, dev_pins, f"{rel(dev_requirements)} must pin dev dependencies with == versions")
        if runtime_requirements.exists() and runtime_lock.exists():
            require(errors, "--hash=sha256:" in runtime_lock.read_text(), f"{rel(runtime_lock)} must be generated with hashes")
            require_lock_contains_pins(errors, runtime_requirements, runtime_lock, runtime_pins)
        if dev_requirements.exists() and dev_lock.exists():
            dev_lock_text = dev_lock.read_text()
            require(errors, "--hash=sha256:" in dev_lock_text, f"{rel(dev_lock)} must be generated with hashes")
            require_lock_contains_pins(errors, runtime_requirements, dev_lock, runtime_pins)
            require_lock_contains_pins(errors, dev_requirements, dev_lock, requirement_pins(dev_requirements))
        if dockerfile.exists():
            dockerfile_text = dockerfile.read_text()
            require(errors, "python:3.14-alpine@sha256:" in dockerfile_text, f"{rel(dockerfile)} must use a pinned Alpine base")
            require(errors, "3.14-slim" not in dockerfile_text, f"{rel(dockerfile)} must not use the Debian slim base")
            require(errors, "COPY requirements.lock ." in dockerfile_text, f"{rel(dockerfile)} must copy the hashed runtime lockfile")
            require(errors, "--require-hashes -r requirements.lock" in dockerfile_text, f"{rel(dockerfile)} must install runtime dependencies with hash checking")

    for script, service in (
        ("scripts/bootstrap-python.sh", "inference-gateway"),
        ("scripts/test-gateway.sh", "inference-gateway"),
        ("scripts/test-rag.sh", "rag-service"),
    ):
        text = (ROOT / script).read_text()
        require(errors, "--require-hashes -r requirements-dev.lock" in text, f"{script} must install hashed dev dependencies for {service}")
        require(errors, "install --upgrade pip" not in text, f"{script} must not upgrade pip from an unpinned network dependency")

    # Ruff is pinned in two places dependabot cannot keep in sync: the quality
    # requirements (CI/local gate) and the pre-commit hook rev. Assert they match
    # so a bump in one cannot silently diverge lint behavior in the other.
    quality_text = (ROOT / "requirements-quality.txt").read_text()
    precommit_text = (ROOT / ".pre-commit-config.yaml").read_text()
    quality_ruff = re.search(r"^ruff==(\S+)$", quality_text, re.MULTILINE)
    precommit_ruff = re.search(r"astral-sh/ruff-pre-commit\s*\n\s*rev:\s*v(\S+)", precommit_text)
    require(errors, quality_ruff is not None, "requirements-quality.txt must pin ruff==<version>")
    require(errors, precommit_ruff is not None, ".pre-commit-config.yaml must pin the ruff-pre-commit rev")
    if quality_ruff and precommit_ruff:
        require(
            errors,
            quality_ruff.group(1) == precommit_ruff.group(1),
            f"ruff pin mismatch: requirements-quality.txt has {quality_ruff.group(1)} "
            f"but .pre-commit-config.yaml rev is v{precommit_ruff.group(1)}",
        )


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


def makefile_targets() -> set[str]:
    text = (ROOT / "Makefile").read_text()
    targets = set(MAKE_TARGET_PATTERN.findall(text))
    for line in text.splitlines():
        if line.startswith(".PHONY:"):
            targets.update(line.removeprefix(".PHONY:").split())
    return targets


def code_segments(path: Path) -> list[tuple[int, str]]:
    lines = enumerate(path.read_text().splitlines(), start=1)
    if path.suffix == ".txt":
        return list(lines)
    segments: list[tuple[int, str]] = []
    in_fence = False
    for number, line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or line.startswith(("    ", "\t")):
            segments.append((number, line))
            continue
        segments.extend((number, span) for span in INLINE_CODE_PATTERN.findall(line))
    return segments


def check_make_target_references(errors: list[str]) -> None:
    # `make <target>` in code spans, code blocks, and the committed quickstart
    # captures must name a real Makefile target so renamed or removed targets
    # cannot linger in operator-facing docs. docs/adr/ and CHANGELOG.md record
    # intentional history and are exempt.
    targets = makefile_targets()
    captures = sorted((ROOT / "docs/assets/quickstart-screenshots").glob("*.txt"))
    for path in markdown_files() + captures:
        relative = rel(path)
        if relative == "CHANGELOG.md" or relative.startswith("docs/adr/"):
            continue
        for number, segment in code_segments(path):
            for name in MAKE_INVOCATION_PATTERN.findall(segment):
                require(errors, name in targets, f"{relative}:{number} references unknown make target: make {name}")


def run_checks() -> list[str]:
    errors: list[str] = []
    check_required_paths(errors)
    check_makefile(errors)
    check_script_modes(errors)
    check_python_bytecode_policy(errors)
    check_toolchain_lookup_policy(errors)
    check_runtime_dependencies(errors)
    check_markdown_links(errors)
    check_make_target_references(errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Check repository hygiene, contributor docs, executable bits, dependencies, markdown links, and make target references.")
    parser.add_argument("--check", action="store_true", help="Run checks and exit non-zero on failures.")
    parser.parse_args()

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
