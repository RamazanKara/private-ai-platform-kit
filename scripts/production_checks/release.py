from __future__ import annotations

import json
import os
import re

import yaml

from .common import (
    ROOT,
    latest_changelog_version,
    load_json,
    nested,
    require,
    require_lock_contains_pins,
    requirement_pins,
)


def check_release_packaging(errors: list[str]) -> None:
    release_version = latest_changelog_version(errors)
    release_tag = f"v{release_version}" if release_version else ""
    workflow_path = ROOT / ".github/workflows/ci.yml"
    require(errors, workflow_path.exists(), "CI workflow must exist")
    if workflow_path.exists():
        workflow = workflow_path.read_text()
        for token in (
            "GATEWAY_TAGS",
            "RAG_TAGS",
            "GITHUB_REF_NAME",
            "steps.build_gateway.outputs.digest",
            "steps.build_rag.outputs.digest",
            "cosign sign --yes",
            "actions/attest-build-provenance@",
            "actions/attest@",
            "attestations: write",
            "push-to-registry: true",
            "supply-chain-checksums.txt",
            "gh release create",
            "gh release upload",
            "--generate-notes",
            "severity: HIGH,CRITICAL",
            'exit-code: "1"',
            "actions/upload-artifact",
            "docker buildx imagetools create",
            "scripts/package-release-charts.py",
            "chart-release-manifest.json",
            "oras push",
            "artifacthub.io",
            "sdk-compatibility",
            "sdk-build",
            "sdk-publish",
            "pypa/gh-action-pypi-publish@cef221092ed1bacb1cc03d23a2d87d1d172e277b",
            "packages-dir: sdk-dist",
        ):
            require(
                errors,
                token in workflow,
                f"CI workflow must publish and sign release supply-chain evidence with {token}",
            )
        require(
            errors,
            "awk '/^Digest:/ {print $2; exit}'" not in workflow,
            "CI digest extraction must not early-exit and SIGPIPE buildx under pipefail",
        )
        # The tested Kubernetes version is pinned in two places: the CI kind node
        # image and the Trivy chart-render version. Keep them moving together.
        kind_match = re.search(r"kindest/node:v(\d+\.\d+\.\d+)", workflow)
        scan_text = (ROOT / "scripts/repo-security-scan.sh").read_text()
        scan_match = re.search(r"--helm-kube-version\s+(\d+\.\d+\.\d+)", scan_text)
        require(errors, kind_match is not None, "CI local-e2e must pin a kindest/node version")
        require(errors, scan_match is not None, "scripts/repo-security-scan.sh must pin --helm-kube-version")
        if kind_match and scan_match:
            require(
                errors,
                kind_match.group(1) == scan_match.group(1),
                "scripts/repo-security-scan.sh --helm-kube-version must match the CI kindest/node version",
            )
    release_script = ROOT / "scripts/package-release-charts.py"
    require(errors, release_script.exists(), "digest-bound Helm chart packaging script must exist")
    require(errors, os.access(release_script, os.X_OK), "digest-bound Helm chart packaging script must be executable")
    for path in (
        ROOT / "artifacthub-repo.yml",
        ROOT / "requirements-sdk-build.lock",
        ROOT / "requirements-sdk-test.lock",
        ROOT / "docs/distribution.md",
    ):
        require(errors, path.exists(), f"release distribution contract missing {path.relative_to(ROOT)}")
    platform_chart = yaml.safe_load((ROOT / "deploy/charts/platform/Chart.yaml").read_text()) or {}
    require(
        errors,
        nested(platform_chart, "annotations", "artifacthub.io/category") == "ai-machine-learning",
        "platform chart must declare its Artifact Hub category",
    )
    docs_workflow = (ROOT / ".github/workflows/docs.yml").read_text()
    for token in ("mike deploy", "gh-pages", 'tags: ["v*"]', "versioned-site"):
        require(errors, token in docs_workflow, f"versioned docs workflow missing {token}")
    scorecard_path = ROOT / ".github/workflows/scorecard.yml"
    require(errors, scorecard_path.exists(), "OpenSSF Scorecard workflow must exist")
    if scorecard_path.exists():
        scorecard = scorecard_path.read_text()
        for token in (
            "ossf/scorecard-action@",
            "results_format: sarif",
            "publish_results: true",
            "github/codeql-action/upload-sarif",
            "security-events: write",
            "id-token: write",
        ):
            require(errors, token in scorecard, f"OpenSSF Scorecard workflow missing {token}")
    gateway_values = yaml.safe_load((ROOT / "deploy/charts/inference-gateway/values.yaml").read_text()) or {}
    rag_values = yaml.safe_load((ROOT / "deploy/charts/rag-service/values.yaml").read_text()) or {}
    gateway_chart = yaml.safe_load((ROOT / "deploy/charts/inference-gateway/Chart.yaml").read_text()) or {}
    rag_chart = yaml.safe_load((ROOT / "deploy/charts/rag-service/Chart.yaml").read_text()) or {}
    gateway_tag = str(nested(gateway_values, "image", "tag", default=""))
    rag_tag = str(nested(rag_values, "image", "tag", default=""))
    gateway_version = str(gateway_chart.get("version", ""))
    rag_version = str(rag_chart.get("version", ""))
    require(
        errors,
        gateway_version == release_version,
        "inference-gateway chart version must match latest CHANGELOG version",
    )
    require(errors, rag_version == release_version, "rag-service chart version must match latest CHANGELOG version")
    require(errors, gateway_tag.startswith("v"), "inference-gateway chart image tag must be a release tag")
    require(errors, rag_tag.startswith("v"), "rag-service chart image tag must be a release tag")
    require(errors, gateway_tag.lstrip("v") == gateway_version, "inference-gateway chart version must match image tag")
    require(errors, rag_tag.lstrip("v") == rag_version, "rag-service chart version must match image tag")
    require(errors, gateway_tag == release_tag, "inference-gateway image tag must match latest CHANGELOG release tag")
    require(errors, rag_tag == release_tag, "rag-service image tag must match latest CHANGELOG release tag")
    require(errors, gateway_tag not in {"latest", "main"}, "inference-gateway chart must not default to floating tags")
    require(errors, rag_tag not in {"latest", "main"}, "rag-service chart must not default to floating tags")
    require(
        errors,
        str(nested(gateway_values, "image", "repository", default="")).split("/", 1)[0] == "ghcr.io",
        "inference-gateway chart must default to a GHCR image",
    )
    require(
        errors,
        str(nested(rag_values, "image", "repository", default="")).split("/", 1)[0] == "ghcr.io",
        "rag-service chart must default to a GHCR image",
    )

    for chart in (
        "agent-workspace",
        "budget-redis",
        "inference-gateway",
        "ollama",
        "qdrant-vector-store",
        "rag-service",
        "vllm",
    ):
        metadata = yaml.safe_load((ROOT / f"deploy/charts/{chart}/Chart.yaml").read_text()) or {}
        require(
            errors,
            metadata.get("version") == gateway_version,
            f"{chart} chart version must match the release chart version",
        )
        if chart in {"agent-workspace", "inference-gateway", "rag-service"}:
            require(
                errors,
                str(metadata.get("appVersion")) == release_version,
                f"{chart} appVersion must match latest CHANGELOG version",
            )

    platform_dependencies = platform_chart.get("dependencies", [])
    require(errors, isinstance(platform_dependencies, list), "platform chart dependencies must be a list")
    if isinstance(platform_dependencies, list):
        for dependency in platform_dependencies:
            if not isinstance(dependency, dict):
                errors.append("platform chart dependencies must be mappings")
                continue
            require(
                errors,
                str(dependency.get("version")) == release_version,
                f"platform dependency {dependency.get('name', '<unnamed>')} must pin release {release_version}",
            )

    platform_lock_path = ROOT / "deploy/charts/platform/Chart.lock"
    require(errors, platform_lock_path.exists(), "platform dependency lock must be committed")
    if platform_lock_path.exists():
        platform_lock = yaml.safe_load(platform_lock_path.read_text()) or {}
        locked_dependencies = platform_lock.get("dependencies", [])
        require(errors, isinstance(locked_dependencies, list), "platform dependency lock must list dependencies")
        if isinstance(locked_dependencies, list):
            require(
                errors,
                len(locked_dependencies) == len(platform_dependencies),
                "platform dependency lock must cover every declared dependency",
            )
            for dependency in locked_dependencies:
                if not isinstance(dependency, dict):
                    errors.append("platform dependency lock entries must be mappings")
                    continue
                require(
                    errors,
                    str(dependency.get("version")) == release_version,
                    f"locked platform dependency {dependency.get('name', '<unnamed>')} must pin release {release_version}",
                )

    for path in ("README.md", "docs/getting-started.md", "deploy/clusters/customer/README.md"):
        text = (ROOT / path).read_text()
        require(errors, f"CUSTOMER_REVISION={release_tag}" in text, f"{path} must show CUSTOMER_REVISION={release_tag}")

    makefile_text = (ROOT / "Makefile").read_text()
    require(
        errors,
        f"CUSTOMER_REVISION ?= {release_tag}" in makefile_text,
        f"Makefile must default CUSTOMER_REVISION to {release_tag}",
    )

    citation_text = (ROOT / "CITATION.cff").read_text()
    require(
        errors,
        f"version: {release_version}" in citation_text,
        f"CITATION.cff version must match latest CHANGELOG version {release_version}",
    )

    index_text = (ROOT / "docs/index.md").read_text()
    require(errors, release_tag in index_text, f"docs/index.md must mention release {release_tag}")

    for path in ("src/inference-gateway/app/main.py", "src/rag-service/app/main.py"):
        text = (ROOT / path).read_text()
        require(
            errors,
            f'SERVICE_VERSION = "{release_version}"' in text,
            f"{path} SERVICE_VERSION must match latest CHANGELOG version",
        )

    for path in (
        "platform/api-contracts/inference-gateway.openapi.json",
        "platform/api-contracts/rag-service.openapi.json",
    ):
        api = load_json(ROOT / path)
        require(
            errors,
            nested(api, "info", "version") == release_version,
            f"{path} info.version must match latest CHANGELOG version",
        )

    for service in ("inference-gateway", "rag-service"):
        dockerignore = ROOT / f"src/{service}/.dockerignore"
        dockerfile = ROOT / f"src/{service}/Dockerfile"
        requirements = ROOT / f"src/{service}/requirements.txt"
        dev_requirements = ROOT / f"src/{service}/requirements-dev.txt"
        runtime_lock = ROOT / f"src/{service}/requirements.lock"
        dev_lock = ROOT / f"src/{service}/requirements-dev.lock"
        require(errors, dockerfile.exists(), f"{service} Dockerfile must exist")
        if dockerfile.exists():
            dockerfile_text = dockerfile.read_text()
            require(
                errors,
                "python:3.14-alpine@sha256:" in dockerfile_text,
                f"{service} Dockerfile must use a pinned Alpine Python base image",
            )
            require(
                errors,
                "python:3.14-slim" not in dockerfile_text,
                f"{service} Dockerfile must not use Debian slim runtime base",
            )
            require(
                errors,
                "COPY requirements.lock ." in dockerfile_text,
                f"{service} Dockerfile must copy the hashed runtime lockfile",
            )
            require(
                errors,
                "--require-hashes -r requirements.lock" in dockerfile_text,
                f"{service} Dockerfile must install runtime dependencies with hash checking",
            )
        require(errors, requirements.exists(), f"{service} runtime requirements must exist")
        runtime_pins: dict[str, str] = {}
        if requirements.exists():
            requirements_text = requirements.read_text()
            runtime_pins = requirement_pins(requirements)
            require(
                errors, "pytest" not in requirements_text, f"{service} runtime requirements must not include pytest"
            )
            require(errors, runtime_pins, f"{service} runtime requirements must pin dependencies with == versions")
        require(errors, dev_requirements.exists(), f"{service} dev requirements must exist")
        if dev_requirements.exists():
            dev_text = dev_requirements.read_text()
            dev_pins = requirement_pins(dev_requirements)
            require(
                errors,
                "-r requirements.txt" in dev_text and "pytest" in dev_text,
                f"{service} dev requirements must extend runtime requirements and include pytest",
            )
            require(errors, dev_pins, f"{service} dev requirements must pin dependencies with == versions")
        require(errors, runtime_lock.exists(), f"{service} runtime lockfile must exist")
        if runtime_lock.exists():
            require(
                errors,
                "--hash=sha256:" in runtime_lock.read_text(),
                f"{service} runtime lockfile must include package hashes",
            )
            require_lock_contains_pins(errors, requirements, runtime_lock, runtime_pins)
        require(errors, dev_lock.exists(), f"{service} dev lockfile must exist")
        if dev_lock.exists():
            dev_lock_text = dev_lock.read_text()
            require(errors, "--hash=sha256:" in dev_lock_text, f"{service} dev lockfile must include package hashes")
            require_lock_contains_pins(errors, requirements, dev_lock, runtime_pins)
            if dev_requirements.exists():
                require_lock_contains_pins(errors, dev_requirements, dev_lock, requirement_pins(dev_requirements))
        require(errors, dockerignore.exists(), f"{service} Docker context must define .dockerignore")
        if dockerignore.exists():
            text = dockerignore.read_text()
            require(
                errors,
                ".venv/" in text and ".pytest_cache/" in text,
                f"{service} .dockerignore must exclude local test environments",
            )

    for script in ("scripts/bootstrap-python.sh", "scripts/test-gateway.sh", "scripts/test-rag.sh"):
        text = (ROOT / script).read_text()
        require(
            errors,
            "--require-hashes -r requirements-dev.lock" in text,
            f"{script} must install hashed dev dependencies",
        )
        require(
            errors,
            "install --upgrade pip" not in text,
            f"{script} must not upgrade pip from an unpinned network dependency",
        )

    image_scan = ROOT / "scripts/image-scan.sh"
    supply_chain_evidence = ROOT / "scripts/supply-chain-evidence.py"
    require(errors, os.access(image_scan, os.X_OK), "scripts/image-scan.sh must be executable")
    require(errors, os.access(supply_chain_evidence, os.X_OK), "scripts/supply-chain-evidence.py must be executable")
    if image_scan.exists():
        image_scan_text = image_scan.read_text()
        for token in ("SYFT_BIN", "spdx-json", "--format sarif", "supply-chain-checksums", "results/supply-chain"):
            require(
                errors,
                token in image_scan_text,
                f"scripts/image-scan.sh must generate local supply-chain evidence with {token}",
            )
        require(
            errors,
            "scripts/supply-chain-evidence.py --summary" in image_scan_text,
            "scripts/image-scan.sh must validate generated supply-chain evidence",
        )
    if supply_chain_evidence.exists():
        supply_chain_text = supply_chain_evidence.read_text()
        for token in ("validate_sbom", "validate_sarif", "parse_checksums", "sha256", "strict-current"):
            require(errors, token in supply_chain_text, f"supply-chain evidence validator must enforce {token}")
    makefile = (ROOT / "Makefile").read_text()
    require(errors, "image-scan:" in makefile, "Makefile must expose image-scan target")
    require(errors, "supply-chain-check:" in makefile, "Makefile must expose supply-chain-check target")

    overlay_script = ROOT / "scripts/configure-customer-overlay.py"
    require(errors, os.access(overlay_script, os.X_OK), "customer overlay configurator must be executable")
    makefile = (ROOT / "Makefile").read_text()
    require(
        errors,
        "customer-overlay:" in makefile and "customer-overlay-check:" in makefile,
        "Makefile must expose customer overlay targets",
    )
    customer_readme = ROOT / "deploy/clusters/customer/README.md"
    require(errors, customer_readme.exists(), "customer deployment guide must exist")


def check_oss_governance(errors: list[str]) -> None:
    """Require the in-tree community and repository-host governance contracts."""
    for path in (
        ROOT / "GOVERNANCE.md",
        ROOT / "MAINTAINERS.md",
        ROOT / "ADOPTERS.md",
        ROOT / "CODE_OF_CONDUCT.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "SECURITY.md",
        ROOT / ".github/repository-settings.json",
        ROOT / "scripts/github-settings.py",
        ROOT / "runbooks/repository-settings.md",
        ROOT / ".github/workflows/fuzz.yml",
        ROOT / "scripts/fuzz-security.py",
    ):
        require(errors, path.exists(), f"OSS governance contract missing {path.relative_to(ROOT)}")
    require(
        errors, os.access(ROOT / "scripts/github-settings.py", os.X_OK), "GitHub settings auditor must be executable"
    )
    require(errors, os.access(ROOT / "scripts/fuzz-security.py", os.X_OK), "security fuzz driver must be executable")
    config = json.loads((ROOT / ".github/repository-settings.json").read_text())
    for key in (
        "private_vulnerability_reporting",
        "vulnerability_alerts",
        "automated_security_fixes",
        "secret_scanning",
        "secret_scanning_push_protection",
    ):
        require(errors, nested(config, "security", key) is True, f"repository security setting {key} must be enabled")
    required_checks = set(nested(config, "branch_protection", "required_status_checks", default=[]))
    require(errors, {"validate", "local-e2e", "security-fuzz"} <= required_checks, "main protection misses core checks")


def check_release_gates(errors: list[str]) -> None:
    config_path = ROOT / "platform/slo/release-gates.yaml"
    script = ROOT / "scripts/release-gate.py"
    require(errors, config_path.exists(), "release gate config must exist at platform/slo/release-gates.yaml")
    require(errors, os.access(script, os.X_OK), "scripts/release-gate.py must be executable")
    require(errors, (ROOT / "runbooks/release-gates.md").exists(), "release gates runbook must exist")
    require(
        errors, (ROOT / "results/release-gate/sample-summary.md").exists(), "release gate sample summary must exist"
    )
    makefile = (ROOT / "Makefile").read_text()
    require(errors, "release-gate-strict:" in makefile, "Makefile must expose release-gate-strict")
    require(errors, "release-report-strict:" in makefile, "Makefile must expose release-report-strict")
    for path in [
        ROOT / "results/evals/sample-summary.json",
        ROOT / "results/loadtest/sample-summary.json",
        ROOT / "results/evidence/sample-summary.json",
        ROOT / "results/toolchain/sample-summary.json",
    ]:
        require(errors, path.exists(), f"release gate sample evidence missing {path.relative_to(ROOT)}")
    if config_path.exists():
        config = yaml.safe_load(config_path.read_text()) or {}
        require(errors, config.get("kind") == "ReleaseGate", "release gate config kind must be ReleaseGate")
        gates = set(nested(config, "spec", "gates", default={}))
        expected = {
            "eval",
            "load",
            "restore",
            "toolchain",
            "egress",
            "retention",
            "slo",
            "quota",
            "modelProvenance",
            "supplyChain",
            "evidencePack",
        }
        require(errors, expected <= gates, f"release gate config missing {sorted(expected - gates)}")
    if script.exists():
        source = script.read_text()
        require(
            errors,
            "--require-current-evidence" in source,
            "release gate script must support current-evidence enforcement",
        )
        require(
            errors,
            "--max-evidence-age-hours" in source,
            "release gate script must support evidence freshness enforcement",
        )
        require(
            errors,
            "check_supply_chain" in source and "supply-chain-evidence.py" in source,
            "release gate script must validate supply-chain evidence",
        )
