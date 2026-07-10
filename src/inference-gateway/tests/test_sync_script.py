"""Regression tests for the fail-closed cluster synchronization boundary."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SYNC = ROOT / "scripts" / "sync.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _fake_toolchain(tmp_path: Path) -> tuple[Path, Path]:
    tool_bin = tmp_path / "bin"
    tool_bin.mkdir()
    command_log = tmp_path / "commands.log"

    _write_executable(
        tool_bin / "kubectl",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'kubectl %s\n' "$*" >>"$COMMAND_LOG"
if [[ "$*" == "config current-context" ]]; then
  printf '%s\n' "${FAKE_CONTEXT:-kind-private-ai-platform-kit}"
elif [[ "${1:-}" == "create" && "${2:-}" == "namespace" ]]; then
  printf 'apiVersion: v1\nkind: Namespace\nmetadata:\n  name: %s\n' "$3"
elif [[ "$*" == "apply -f -" ]]; then
  cat >/dev/null
elif [[ "$*" == *"jsonpath="* ]]; then
  printf '%s\n' "${FAKE_APP_STATE:-Synced|Healthy|}"
fi
""",
    )
    _write_executable(
        tool_bin / "helm",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'helm %s\n' "$*" >>"$COMMAND_LOG"
""",
    )
    _write_executable(
        tool_bin / "argocd",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'argocd %s\n' "$*" >>"$COMMAND_LOG"
if [[ "${2:-}" == "sync" && "${FAKE_ARGOCD_MODE:-success}" == "repository-error" ]]; then
  printf 'rpc error: repository not found\n' >&2
  exit 20
fi
if [[ "${2:-}" == "sync" && "${FAKE_ARGOCD_MODE:-success}" == "generic-error" ]]; then
  printf 'rpc error: reconciliation failed\n' >&2
  exit 17
fi
printf 'ok\n'
""",
    )
    return tool_bin, command_log


def _run_sync(tmp_path: Path, **overrides: str) -> tuple[subprocess.CompletedProcess[str], str]:
    tool_bin, command_log = _fake_toolchain(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "TOOLCHAIN_BIN_DIR": str(tool_bin),
            "COMMAND_LOG": str(command_log),
            "ARGO_SYNC_TIMEOUT": "2",
            "ARGO_POLL_INTERVAL": "1",
        }
    )
    env.update(overrides)
    result = subprocess.run(
        ["bash", str(SYNC)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    commands = command_log.read_text(encoding="utf-8") if command_log.exists() else ""
    return result, commands


def test_customer_repository_failure_never_falls_back_to_helm(tmp_path: Path) -> None:
    result, commands = _run_sync(
        tmp_path,
        ENVIRONMENT="customer",
        FAKE_ARGOCD_MODE="repository-error",
        FAKE_CONTEXT="production-cluster",
    )

    assert result.returncode != 0
    assert "Customer sync is fail-closed" in result.stderr
    assert "helm " not in commands
    assert "kubectl apply" not in commands


def test_customer_direct_apply_is_always_rejected(tmp_path: Path) -> None:
    result, commands = _run_sync(
        tmp_path,
        ENVIRONMENT="customer",
        LOCAL_DIRECT_APPLY="1",
        FAKE_CONTEXT="kind-private-ai-platform-kit",
    )

    assert result.returncode != 0
    assert "local direct apply is disabled" in result.stderr
    assert "helm " not in commands
    assert "kubectl apply" not in commands


def test_local_direct_apply_requires_the_expected_context(tmp_path: Path) -> None:
    result, commands = _run_sync(
        tmp_path,
        ENVIRONMENT="local",
        LOCAL_DIRECT_APPLY="1",
        FAKE_CONTEXT="production-cluster",
    )

    assert result.returncode != 0
    assert "refusing local direct apply" in result.stderr
    assert "helm " not in commands
    assert "kubectl apply" not in commands


def test_local_direct_apply_centrally_owns_namespaces(tmp_path: Path) -> None:
    result, commands = _run_sync(
        tmp_path,
        ENVIRONMENT="local",
        LOCAL_DIRECT_APPLY="1",
        FAKE_CONTEXT="kind-private-ai-platform-kit",
    )

    assert result.returncode == 0, result.stderr
    helm_commands = [line for line in commands.splitlines() if line.startswith("helm ")]
    assert len(helm_commands) == 6
    assert all("--set namespace.create=false" in command for command in helm_commands)
    assert "--create-namespace" not in commands
    for namespace in ("ollama", "budget", "inference", "rag", "vector", "ai-agents"):
        assert f"kubectl label namespace {namespace}" in commands


def test_generic_argocd_error_is_not_swallowed(tmp_path: Path) -> None:
    result, commands = _run_sync(
        tmp_path,
        ENVIRONMENT="local",
        FAKE_ARGOCD_MODE="generic-error",
    )

    assert result.returncode != 0
    assert "argocd app sync failed with status 17" in result.stderr
    assert "argocd app wait" not in commands
    assert "helm " not in commands


def test_customer_sync_waits_for_declared_applications(tmp_path: Path) -> None:
    result, commands = _run_sync(tmp_path, ENVIRONMENT="customer")

    assert result.returncode == 0, result.stderr
    assert "all Argo CD applications are Synced and Healthy" in result.stdout
    assert "get application private-ai-platform-kit-root" in commands
    assert "get application inference-gateway" in commands
    assert "get application rag-service" in commands
    assert "helm " not in commands
