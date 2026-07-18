from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHANGELOG_VERSION_PATTERN = re.compile(r"^## v(?P<version>\d+\.\d+\.\d+) - \d{4}-\d{2}-\d{2}$")
PIN_PATTERN = re.compile(r"^\s*([A-Za-z0-9_.-]+)==([^\s\\]+)")


def load_yaml_documents(path: Path) -> list[dict[str, Any]]:
    docs = []
    for item in yaml.safe_load_all(path.read_text()):
        if isinstance(item, dict):
            docs.append(item)
    return docs


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def latest_changelog_version(errors: list[str]) -> str:
    changelog = ROOT / "CHANGELOG.md"
    require(errors, changelog.exists(), "CHANGELOG.md must exist")
    if not changelog.exists():
        return ""
    for line in changelog.read_text().splitlines():
        match = CHANGELOG_VERSION_PATTERN.fullmatch(line.strip())
        if match:
            return match.group("version")
    errors.append("CHANGELOG.md must start with a version heading like '## v0.0.0 - YYYY-MM-DD'")
    return ""


def render_chart(chart: str, values: Path | None = None) -> list[dict[str, Any]]:
    cmd = ["helm", "template", "production-check", str(ROOT / f"deploy/charts/{chart}")]
    if values:
        cmd.extend(["--values", str(values)])
    rendered = subprocess.run(cmd, check=True, text=True, capture_output=True)
    return [doc for doc in yaml.safe_load_all(rendered.stdout) if isinstance(doc, dict)]


def find_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [doc for doc in docs if doc.get("kind") == kind]


def env_names(deployment: dict[str, Any]) -> set[str]:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    env = containers[0].get("env", [])
    return {item.get("name") for item in env}


def container(deployment: dict[str, Any]) -> dict[str, Any]:
    return deployment["spec"]["template"]["spec"]["containers"][0]


def nested(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return current if current is not None else default


def require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


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
    lock_text = lockfile.read_text().lower()
    for name, version in expected.items():
        require(
            errors,
            f"{name}=={version}" in lock_text,
            f"{lockfile.relative_to(ROOT)} must include pinned dependency {name}=={version} "
            f"from {requirements.relative_to(ROOT)}",
        )
