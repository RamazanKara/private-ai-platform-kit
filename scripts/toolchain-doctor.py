#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "platform/tools/validation-toolchain.yaml"


@dataclass(frozen=True)
class ToolResult:
    name: str
    role: str
    command: str
    category: str
    found: bool
    path: str
    version: str
    version_error: str
    purpose: str
    install_hint: str


def load_manifest(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def nested(mapping: Any, *keys: str, default: Any = None) -> Any:
    current = mapping
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return current if current is not None else default


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def toolchain_bin_dirs() -> list[Path]:
    candidates: list[Path] = []
    env_bin_dir = os.getenv("TOOLCHAIN_BIN_DIR")
    if env_bin_dir:
        candidates.append(Path(env_bin_dir).expanduser())
    candidates.append(ROOT / ".tools/bin")
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate if candidate.is_absolute() else ROOT / candidate
        if resolved not in seen:
            unique.append(resolved)
            seen.add(resolved)
    return unique


def command_path(command: str) -> str:
    for candidate in [directory / command for directory in toolchain_bin_dirs()]:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which(command) or ""


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("apiVersion") != "platform.ai/v1alpha1":
        errors.append("validation toolchain apiVersion must be platform.ai/v1alpha1")
    if manifest.get("kind") != "ValidationToolchain":
        errors.append("validation toolchain kind must be ValidationToolchain")

    profiles = nested(manifest, "spec", "profiles", default={})
    tools = nested(manifest, "spec", "tools", default={})
    if not isinstance(profiles, dict) or not profiles:
        errors.append("spec.profiles must be a non-empty mapping")
    if not isinstance(tools, dict) or not tools:
        errors.append("spec.tools must be a non-empty mapping")
        return errors

    for name, tool in tools.items():
        if not isinstance(tool, dict):
            errors.append(f"tool {name} must be a mapping")
            continue
        if not tool.get("command"):
            errors.append(f"tool {name} must define command")
        version_command = tool.get("versionCommand")
        if version_command is not None and (
            not isinstance(version_command, list) or not all(isinstance(item, str) and item for item in version_command)
        ):
            errors.append(f"tool {name} versionCommand must be a non-empty string list")
        for field in ("category", "purpose", "installHint"):
            if not tool.get(field):
                errors.append(f"tool {name} must define {field}")

    if isinstance(profiles, dict):
        for profile_name, profile in profiles.items():
            if not isinstance(profile, dict):
                errors.append(f"profile {profile_name} must be a mapping")
                continue
            required = profile.get("required", [])
            optional = profile.get("optional", [])
            for field, items in (("required", required), ("optional", optional)):
                if not isinstance(items, list) or not all(isinstance(item, str) and item for item in items):
                    errors.append(f"profile {profile_name}.{field} must be a string list")
                    continue
                missing = sorted(set(items) - set(tools))
                if missing:
                    errors.append(f"profile {profile_name}.{field} references unknown tools {missing}")
            overlap = sorted(set(required) & set(optional)) if isinstance(required, list) and isinstance(optional, list) else []
            if overlap:
                errors.append(f"profile {profile_name} lists tools as both required and optional: {overlap}")
    return errors


def version_for(tool: dict[str, Any], command_path: str) -> tuple[str, str]:
    version_command = tool.get("versionCommand")
    if not isinstance(version_command, list) or not version_command:
        return "", ""
    cmd = [str(part) for part in version_command]
    if cmd[0] == str(tool.get("command")):
        cmd[0] = command_path
    try:
        completed = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", str(exc)
    output = (completed.stdout or completed.stderr).strip()
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        payload = {}
    if isinstance(payload, dict):
        for key in ("gitVersion", "version"):
            value = payload.get(key)
            if value:
                return str(value), "" if completed.returncode == 0 else f"version command exited {completed.returncode}"
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    preferred_prefixes = ("Version:", "GitVersion:", "k6 ", "argocd:", "Client Version:", "Docker version", "kind ")
    first_line = next((line for line in lines for prefix in preferred_prefixes if line.startswith(prefix)), lines[0] if lines else "")
    if completed.returncode != 0:
        return first_line, f"version command exited {completed.returncode}"
    return first_line, ""


def check_tools(manifest: dict[str, Any], profile_name: str) -> tuple[list[ToolResult], list[str]]:
    profiles = nested(manifest, "spec", "profiles", default={})
    tools = nested(manifest, "spec", "tools", default={})
    if profile_name not in profiles:
        return [], [f"unknown profile {profile_name}; expected one of {sorted(profiles)}"]
    profile = profiles[profile_name]
    results: list[ToolResult] = []
    for role, names in (("required", profile.get("required", [])), ("optional", profile.get("optional", []))):
        for name in names:
            tool = tools[name]
            command = str(tool["command"])
            tool_command_path = command_path(command)
            version = ""
            version_error = ""
            if tool_command_path:
                version, version_error = version_for(tool, tool_command_path)
            results.append(
                ToolResult(
                    name=name,
                    role=role,
                    command=command,
                    category=str(tool.get("category", "")),
                    found=bool(tool_command_path),
                    path=tool_command_path,
                    version=version,
                    version_error=version_error,
                    purpose=str(tool.get("purpose", "")),
                    install_hint=str(tool.get("installHint", "")),
                )
            )
    return results, []


def summarize(profile: str, results: list[ToolResult]) -> str:
    required = [item for item in results if item.role == "required"]
    optional = [item for item in results if item.role == "optional"]
    missing_required = [item.name for item in required if not item.found]
    missing_optional = [item.name for item in optional if not item.found]
    lines = [
        f"validation toolchain profile {profile}: "
        f"{len(required) - len(missing_required)}/{len(required)} required present, "
        f"{len(optional) - len(missing_optional)}/{len(optional)} optional present"
    ]
    if missing_required:
        lines.append("missing required: " + ", ".join(missing_required))
    if missing_optional:
        lines.append("missing optional: " + ", ".join(missing_optional))
    return "\n".join(lines)


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def write_markdown(path: Path, generated_at: str, profile: str, results: list[ToolResult], manifest_path: Path) -> None:
    missing_required = [item for item in results if item.role == "required" and not item.found]
    missing_optional = [item for item in results if item.role == "optional" and not item.found]
    lines = [
        "# Validation Toolchain Report",
        "",
        f"Generated: `{generated_at}`",
        f"Profile: `{profile}`",
        f"Manifest: `{rel(manifest_path)}`",
        "",
        f"Summary: {len(missing_required)} missing required tools, {len(missing_optional)} missing optional tools.",
        "",
        "| Tool | Role | Status | Version | Purpose | Install hint |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        status = "found" if item.found else "missing"
        version = item.version or item.path or item.version_error or ""
        lines.append(
            "| "
            + " | ".join(
                [
                    item.name,
                    item.role,
                    status,
                    markdown_escape(version),
                    markdown_escape(item.purpose),
                    markdown_escape(item.install_hint),
                ]
            )
            + " |"
        )
    path.write_text("\n".join(lines) + "\n")


def write_json(path: Path, generated_at: str, profile: str, results: list[ToolResult], manifest_path: Path) -> None:
    missing_required = [item.name for item in results if item.role == "required" and not item.found]
    missing_optional = [item.name for item in results if item.role == "optional" and not item.found]
    payload = {
        "project": "Private AI Platform Kit",
        "generated_at": generated_at,
        "profile": profile,
        "manifest": rel(manifest_path),
        "summary": {
            "missing_required": missing_required,
            "missing_optional": missing_optional,
            "required_present": sum(1 for item in results if item.role == "required" and item.found),
            "optional_present": sum(1 for item in results if item.role == "optional" and item.found),
        },
        "tools": [asdict(item) for item in results],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the Private AI Platform Kit validation toolchain.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="ValidationToolchain manifest path.")
    parser.add_argument("--profile", default="validate", help="Tool profile to check: validate, local, or strict.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero when required tools are missing.")
    parser.add_argument("--report", action="store_true", help="Write JSON and Markdown reports.")
    parser.add_argument("--output-dir", default="results/toolchain", help="Directory for report output.")
    args = parser.parse_args()

    manifest_path = (ROOT / args.manifest).resolve() if not Path(args.manifest).is_absolute() else Path(args.manifest)
    try:
        manifest = load_manifest(manifest_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"failed to load validation toolchain manifest: {exc}")
        return 1

    errors = validate_manifest(manifest)
    results: list[ToolResult] = []
    if not errors:
        results, profile_errors = check_tools(manifest, args.profile)
        errors.extend(profile_errors)

    if errors:
        print("validation toolchain manifest check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(summarize(args.profile, results))
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if args.report:
        output_dir = ROOT / args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        json_path = output_dir / f"toolchain-{stamp}.json"
        md_path = output_dir / f"toolchain-{stamp}.md"
        write_json(json_path, generated_at, args.profile, results, manifest_path)
        write_markdown(md_path, generated_at, args.profile, results, manifest_path)
        print(f"wrote {rel(json_path)} and {rel(md_path)}")

    missing_required = [item for item in results if item.role == "required" and not item.found]
    if args.check and missing_required:
        for item in missing_required:
            print(f"- install {item.name}: {item.install_hint}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
