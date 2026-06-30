#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROOT_APP = ROOT / "deploy/gitops/argocd/root-app-customer.yaml"
CUSTOMER_APPS = ROOT / "deploy/clusters/customer/apps.yaml"
APPPROJECTS = ROOT / "deploy/clusters/customer/appprojects.yaml"
VLLM_VALUE_FILES = {
    "default": "../../clusters/customer/values/vllm.yaml",
    "nvidia": "../../clusters/customer/values/vllm-nvidia.yaml",
    "amd": "../../clusters/customer/values/vllm-amd.yaml",
}


def valid_git_url(value: str) -> bool:
    return value.startswith(("https://", "ssh://", "git@"))


def is_pinned_revision(rev: str) -> bool:
    """True only for an immutable ref: a semver-ish tag or a commit SHA."""
    return bool(
        re.fullmatch(r"v?\d+\.\d+\.\d+([.-].+)?", rev)
        or re.fullmatch(r"[0-9a-f]{7,40}", rev)
    )


def load_yaml_documents(path: Path) -> list[dict[str, Any]]:
    documents = list(yaml.safe_load_all(path.read_text()))
    if not documents or not all(isinstance(document, dict) for document in documents):
        raise ValueError(f"{path.relative_to(ROOT)} must contain YAML mapping documents")
    return documents


def write_yaml_documents(path: Path, documents: list[dict[str, Any]]) -> None:
    rendered = "\n---\n".join(yaml.safe_dump(document, sort_keys=False).strip() for document in documents)
    path.write_text(rendered + "\n")


def nested(mapping: dict[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def value_file_exists(application: dict[str, Any], value_file: str) -> bool:
    source_path = nested(application, "spec", "source", "path")
    if not isinstance(source_path, str):
        return False
    return (ROOT / source_path / value_file).resolve().is_file()


def check_overlay() -> list[str]:
    errors: list[str] = []
    try:
        root_docs = load_yaml_documents(ROOT_APP)
        app_docs = load_yaml_documents(CUSTOMER_APPS)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return [str(exc)]

    require(errors, len(root_docs) == 1, "root-app-customer.yaml must contain exactly one Application")
    root = root_docs[0]
    root_repo = nested(root, "spec", "source", "repoURL")
    root_revision = nested(root, "spec", "source", "targetRevision")
    require(errors, root.get("kind") == "Application", "root-app-customer.yaml must define an Argo CD Application")
    require(errors, nested(root, "spec", "source", "path") == "deploy/clusters/customer", "customer root app must point at deploy/clusters/customer")
    require(errors, isinstance(root_repo, str) and valid_git_url(root_repo), "customer root repoURL must be a Git URL")
    require(errors, isinstance(root_revision, str) and bool(root_revision), "customer root targetRevision must be set")
    require(errors, is_pinned_revision(str(root_revision)), "customer root targetRevision must be pinned to a tag or commit SHA, not HEAD or a branch")

    require(errors, APPPROJECTS.exists(), "deploy/clusters/customer/appprojects.yaml must exist")
    if APPPROJECTS.exists():
        try:
            project_docs = load_yaml_documents(APPPROJECTS)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            project_docs = []
            errors.append(str(exc))
        platform_project = next(
            (doc for doc in project_docs if nested(doc, "metadata", "name") == "private-ai-platform"),
            None,
        )
        require(errors, platform_project is not None, "appprojects.yaml must contain an AppProject named private-ai-platform")
        if platform_project is not None:
            require(
                errors,
                nested(platform_project, "spec", "sourceRepos") == [root_repo],
                "private-ai-platform AppProject sourceRepos must equal the customer root repoURL",
            )

    application_names: set[str] = set()
    runtime_vllm: dict[str, Any] | None = None
    for application in app_docs:
        name = nested(application, "metadata", "name")
        source = nested(application, "spec", "source")
        require(errors, application.get("kind") == "Application", f"{name or '<unknown>'} must be an Argo CD Application")
        require(errors, isinstance(name, str) and bool(name), "customer child application must have metadata.name")
        if isinstance(name, str):
            require(errors, name not in application_names, f"duplicate customer child application {name}")
            application_names.add(name)
        require(errors, isinstance(source, dict), f"{name}: spec.source must be set")
        require(errors, nested(application, "spec", "source", "repoURL") == root_repo, f"{name}: repoURL must match root-app-customer.yaml")
        require(errors, nested(application, "spec", "source", "targetRevision") == root_revision, f"{name}: targetRevision must match root-app-customer.yaml")
        require(errors, nested(application, "spec", "project") == "private-ai-platform", f"{name}: must use the private-ai-platform AppProject, not project: default")
        source_path = nested(application, "spec", "source", "path")
        require(errors, isinstance(source_path, str) and (ROOT / source_path).exists(), f"{name}: source.path must exist")
        value_files = nested(application, "spec", "source", "helm", "valueFiles") or []
        require(errors, isinstance(value_files, list), f"{name}: helm.valueFiles must be a list when set")
        for value_file in value_files if isinstance(value_files, list) else []:
            require(errors, isinstance(value_file, str) and value_file_exists(application, value_file), f"{name}: Helm value file is missing: {value_file}")
        if name == "runtime-vllm":
            runtime_vllm = application

    expected = {
        "model-catalog",
        "traceable-sandbox",
        "runtime-ollama",
        "runtime-vllm",
        "budget-redis",
        "inference-gateway",
        "qdrant-vector-store",
        "rag-service",
        "agent-workspace",
        "security-policies",
        "restore-drill",
    }
    require(errors, expected <= application_names, f"customer apps missing {sorted(expected - application_names)}")

    if runtime_vllm is None:
        errors.append("runtime-vllm application is required")
    else:
        value_files = nested(runtime_vllm, "spec", "source", "helm", "valueFiles") or []
        active = [value for value in value_files if value in VLLM_VALUE_FILES.values()]
        require(errors, len(active) == 1, "runtime-vllm must use exactly one supported customer vLLM values file")

    return errors


def configure_overlay(repo_url: str, target_revision: str, gpu_profile: str, dry_run: bool) -> None:
    root_docs = load_yaml_documents(ROOT_APP)
    app_docs = load_yaml_documents(CUSTOMER_APPS)
    value_file = VLLM_VALUE_FILES[gpu_profile]

    root_docs[0]["spec"]["source"]["repoURL"] = repo_url
    root_docs[0]["spec"]["source"]["targetRevision"] = target_revision

    for application in app_docs:
        source = application["spec"]["source"]
        source["repoURL"] = repo_url
        source["targetRevision"] = target_revision
        if nested(application, "metadata", "name") == "runtime-vllm":
            source.setdefault("helm", {})["valueFiles"] = [value_file]

    if not dry_run:
        write_yaml_documents(ROOT_APP, root_docs)
        write_yaml_documents(CUSTOMER_APPS, app_docs)

    if APPPROJECTS.exists():
        project_docs = load_yaml_documents(APPPROJECTS)
        for project in project_docs:
            project.setdefault("spec", {})["sourceRepos"] = [repo_url]
        if not dry_run:
            write_yaml_documents(APPPROJECTS, project_docs)

    action = "would configure" if dry_run else "configured"
    print(f"{action} customer overlay: repo={repo_url}, revision={target_revision}, gpu_profile={gpu_profile}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure and validate the customer-owned Kubernetes GitOps overlay.")
    parser.add_argument("--repo-url", default="https://github.com/RamazanKara/private-ai-platform-kit.git")
    parser.add_argument("--target-revision", default="v0.11.0")
    parser.add_argument("--gpu-profile", choices=sorted(VLLM_VALUE_FILES), default="nvidia")
    parser.add_argument("--check", action="store_true", help="Validate the current overlay without modifying files.")
    parser.add_argument("--dry-run", action="store_true", help="Print the requested configuration without writing files.")
    args = parser.parse_args()

    if args.check:
        errors = check_overlay()
        if errors:
            print("customer overlay check failed:")
            for error in errors:
                print(f"- {error}")
            return 1
        print("customer overlay OK")
        return 0

    try:
        repo_url = args.repo_url.strip()
        target_revision = args.target_revision.strip()
        if not valid_git_url(repo_url):
            raise ValueError("--repo-url must start with https://, ssh://, or git@")
        if not target_revision:
            raise ValueError("--target-revision must not be empty")
        configure_overlay(repo_url, target_revision, args.gpu_profile, args.dry_run)
    except (OSError, ValueError, yaml.YAMLError, KeyError) as exc:
        print(f"failed to configure customer overlay: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
