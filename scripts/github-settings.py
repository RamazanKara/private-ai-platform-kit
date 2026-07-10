#!/usr/bin/env python3
"""Audit or explicitly apply the repository's GitHub security/governance settings."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".github/repository-settings.json"


def gh(method: str, endpoint: str, payload: dict[str, Any] | None = None, *, tolerate_404: bool = False) -> Any:
    command = ["gh", "api", "--method", method, endpoint]
    if payload is not None:
        command += ["--input", "-"]
    result = subprocess.run(
        command,
        input=json.dumps(payload) if payload is not None else None,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        if tolerate_404 and "HTTP 404" in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip() or f"gh api failed for {endpoint}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def repository() -> str:
    result = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def enabled_endpoint(repo: str, endpoint: str) -> bool:
    result = subprocess.run(
        ["gh", "api", "--method", "GET", f"repos/{repo}/{endpoint}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    value = json.loads(result.stdout) if result.stdout.strip() else None
    if isinstance(value, dict) and "enabled" in value:
        return bool(value["enabled"])
    # The vulnerability-alerts and automated-security-fixes endpoints signal an
    # enabled state with HTTP 204 and intentionally return no JSON body.
    return True


def audit(repo: str, config: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    metadata = gh("GET", f"repos/{repo}")
    for key, expected in config["features"].items():
        actual = metadata.get("has_discussions" if key == "discussions" else key)
        if actual != expected:
            failures.append(f"features.{key}: expected {expected!r}, got {actual!r}")
    if metadata.get("homepage") != config["metadata"]["homepage"]:
        failures.append(
            f"metadata.homepage: expected {config['metadata']['homepage']!r}, got {metadata.get('homepage')!r}"
        )
    topics = set((gh("GET", f"repos/{repo}/topics") or {}).get("names") or [])
    expected_topics = set(config["metadata"]["topics"])
    if topics != expected_topics:
        failures.append(f"metadata.topics: expected {sorted(expected_topics)}, got {sorted(topics)}")
    analysis = metadata.get("security_and_analysis") or {}
    for key in ("secret_scanning", "secret_scanning_push_protection"):
        actual = (analysis.get(key) or {}).get("status") == "enabled"
        if actual != config["security"][key]:
            failures.append(f"security.{key}: expected enabled={config['security'][key]}, got {actual}")
    endpoint_checks = {
        "private_vulnerability_reporting": "private-vulnerability-reporting",
        "vulnerability_alerts": "vulnerability-alerts",
        "automated_security_fixes": "automated-security-fixes",
    }
    for key, endpoint in endpoint_checks.items():
        actual = enabled_endpoint(repo, endpoint)
        if actual != config["security"][key]:
            failures.append(f"security.{key}: expected {config['security'][key]}, got {actual}")

    branch = config["default_branch"]
    protection = gh("GET", f"repos/{repo}/branches/{branch}/protection", tolerate_404=True)
    if protection is None:
        failures.append(f"branch_protection: {branch} is not protected")
    else:
        required = protection.get("required_status_checks") or {}
        contexts = set(required.get("contexts") or [])
        expected_contexts = set(config["branch_protection"]["required_status_checks"])
        missing = sorted(expected_contexts - contexts)
        if missing:
            failures.append(f"branch_protection.required_status_checks missing {missing}")
        if bool(required.get("strict")) != config["branch_protection"]["strict"]:
            failures.append("branch_protection.strict does not match")
    for name, expected in config.get("environments", {}).items():
        environment = gh("GET", f"repos/{repo}/environments/{name}", tolerate_404=True)
        if environment is None:
            failures.append(f"environments.{name}: missing")
            continue
        reviewer_names = {
            item.get("reviewer", {}).get("login")
            for rule in environment.get("protection_rules", [])
            if rule.get("type") == "required_reviewers"
            for item in rule.get("reviewers", [])
        }
        if reviewer_names != set(expected["reviewers"]):
            failures.append(
                f"environments.{name}.reviewers: expected {expected['reviewers']}, got {sorted(reviewer_names)}"
            )
        policies = gh("GET", f"repos/{repo}/environments/{name}/deployment-branch-policies") or {}
        found = {(item.get("name"), item.get("type")) for item in policies.get("branch_policies", [])}
        if (expected["tag_pattern"], "tag") not in found:
            failures.append(f"environments.{name}: missing tag policy {expected['tag_pattern']}")
    return failures


def apply(repo: str, config: dict[str, Any]) -> None:
    features = config["features"]
    gh(
        "PATCH",
        f"repos/{repo}",
        {
            "has_discussions": features["discussions"],
            "delete_branch_on_merge": features["delete_branch_on_merge"],
            "allow_squash_merge": features["allow_squash_merge"],
            "allow_merge_commit": features["allow_merge_commit"],
            "allow_rebase_merge": features["allow_rebase_merge"],
            "homepage": config["metadata"]["homepage"],
            "security_and_analysis": {
                "secret_scanning": {"status": "enabled"},
                "secret_scanning_push_protection": {"status": "enabled"},
            },
        },
    )
    gh("PUT", f"repos/{repo}/topics", {"names": config["metadata"]["topics"]})
    for endpoint in ("private-vulnerability-reporting", "vulnerability-alerts", "automated-security-fixes"):
        gh("PUT", f"repos/{repo}/{endpoint}")
    branch = config["default_branch"]
    bp = config["branch_protection"]
    gh(
        "PUT",
        f"repos/{repo}/branches/{branch}/protection",
        {
            "required_status_checks": {
                "strict": bp["strict"],
                "contexts": bp["required_status_checks"],
            },
            "enforce_admins": bp["enforce_admins"],
            "required_pull_request_reviews": {
                "dismiss_stale_reviews": bp["dismiss_stale_reviews"],
                "required_approving_review_count": bp["required_approving_review_count"],
            },
            "restrictions": None,
            "required_conversation_resolution": bp["require_conversation_resolution"],
            "allow_force_pushes": bp["allow_force_pushes"],
            "allow_deletions": bp["allow_deletions"],
        },
    )
    for name, desired in config.get("environments", {}).items():
        reviewers = []
        for login in desired["reviewers"]:
            user = gh("GET", f"users/{login}")
            reviewers.append({"type": "User", "id": user["id"]})
        gh(
            "PUT",
            f"repos/{repo}/environments/{name}",
            {
                "wait_timer": 0,
                "prevent_self_review": desired["prevent_self_review"],
                "reviewers": reviewers,
                "deployment_branch_policy": {
                    "protected_branches": False,
                    "custom_branch_policies": True,
                },
            },
        )
        policies = gh("GET", f"repos/{repo}/environments/{name}/deployment-branch-policies") or {}
        found = {(item.get("name"), item.get("type")) for item in policies.get("branch_policies", [])}
        if (desired["tag_pattern"], "tag") not in found:
            gh(
                "POST",
                f"repos/{repo}/environments/{name}/deployment-branch-policies",
                {"name": desired["tag_pattern"], "type": "tag"},
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply desired settings before auditing")
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    repo = repository()
    if args.apply:
        apply(repo, config)
    failures = audit(repo, config)
    if failures:
        print(f"GitHub repository settings drift for {repo}:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"GitHub repository settings match {CONFIG.relative_to(ROOT)} for {repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
