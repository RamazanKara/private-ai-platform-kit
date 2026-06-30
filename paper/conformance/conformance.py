#!/usr/bin/env python3
"""Conformance suite: attempt forbidden actions against a fully governed gateway and
assert that each is blocked, plus one positive control that a legitimate request passes.

Each blocked attempt is an evidence item for the access-and-model-governance control
(requirements R5, R6, R8 in the paper). The suite starts the real gateway in its full
governance configuration against a mock runtime, fires a fixed set of requests, checks
the status code and the machine-readable rejection reason, and writes an evidence file.

Run under the gateway virtualenv interpreter.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
COC = HERE.parent / "cost-of-compliance"
_DEFAULT_GATEWAY_DIR = str(Path(__file__).resolve().parents[2] / "src" / "inference-gateway")
GATEWAY_DIR = Path(os.environ.get("GATEWAY_DIR", _DEFAULT_GATEWAY_DIR))
PYTHON = sys.executable

MOCK_PORT = int(os.environ.get("CONF_MOCK_PORT", "9088"))
GW_PORT = int(os.environ.get("CONF_GW_PORT", "8088"))
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"
GW_URL = f"http://127.0.0.1:{GW_PORT}"

API_KEY = "conformance-key"
API_KEY_SHA256 = hashlib.sha256(API_KEY.encode()).hexdigest()
MODEL = "approved-model"

GATEWAY_ENV = {
    "RUNTIME_BACKEND": "ollama",
    "OLLAMA_BASE_URL": MOCK_URL,
    "MODEL_ID": MODEL,
    "ALLOWED_MODELS": MODEL,
    "API_KEY_AUTH_ENABLED": "true",
    "API_KEY_SHA256S": API_KEY_SHA256,
    "PROMPT_SECRET_DETECTION_ENABLED": "true",
    "AUDIT_LOG_ENABLED": "true",
    "ALLOW_STREAMING": "false",
    "MAX_PROMPT_CHARS": "200",
    "MAX_MESSAGES": "4",
    "MAX_COMPLETION_TOKENS": "64",
    "SANDBOX_BUDGET_ENABLED": "true",
    "SANDBOX_REQUEST_BUDGET": "1",
    "SANDBOX_BUDGET_WINDOW_SECONDS": "3600",
    "REQUEST_TIMEOUT_SECONDS": "30",
}


def wait_healthy(url: str, timeout: float = 25.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/healthz", timeout=2.0).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{url} not healthy")


def post(path_payload: dict, headers: dict) -> httpx.Response:
    return httpx.post(f"{GW_URL}/v1/chat/completions", json=path_payload, headers=headers, timeout=10.0)


def auth(sandbox: str) -> dict:
    return {"X-API-Key": API_KEY, "X-Sandbox-ID": sandbox, "Content-Type": "application/json"}


def msg(content: str) -> dict:
    return {"role": "user", "content": content}


def reason_of(resp: httpx.Response) -> str | None:
    try:
        detail = resp.json().get("detail")
        if isinstance(detail, dict):
            return detail.get("reason")
    except Exception:
        return None
    return None


def run_checks() -> list[dict]:
    checks: list[dict] = []

    def record(name, resp, exp_status, exp_reason=None):
        actual_reason = reason_of(resp)
        passed = resp.status_code == exp_status and (exp_reason is None or actual_reason == exp_reason)
        checks.append({
            "check": name,
            "expected_status": exp_status,
            "actual_status": resp.status_code,
            "expected_reason": exp_reason,
            "actual_reason": actual_reason,
            "passed": passed,
        })

    # Positive control: a legitimate, authorized request must succeed.
    record("authorized_request_succeeds",
           post({"model": MODEL, "messages": [msg("hello")]}, auth("ok-1")), 200)

    # T3: unauthenticated request is rejected at the gateway boundary.
    record("unauthenticated_rejected",
           httpx.post(f"{GW_URL}/v1/chat/completions",
                      json={"model": MODEL, "messages": [msg("hello")]},
                      headers={"X-Sandbox-ID": "no-auth"}, timeout=10.0), 401)

    # R6 / T2: an unapproved model identifier is rejected.
    record("unapproved_model_rejected",
           post({"model": "not-approved", "messages": [msg("hello")]}, auth("conf-1")),
           400, "model_not_allowed")

    # R3 boundary: an oversized prompt is rejected before forwarding.
    record("oversized_prompt_rejected",
           post({"model": MODEL, "messages": [msg("x" * 400)]}, auth("conf-2")),
           400, "prompt_too_large")

    # T1: a prompt carrying credential material is refused, not forwarded or logged raw.
    record("prompt_secret_rejected",
           post({"model": MODEL, "messages": [msg("api_key: 'AKIAIOSFODNN7EXAMPLEKEY12345'")]}, auth("conf-3")),
           400, "prompt_secret_detected")

    # Admission: streaming disabled.
    record("streaming_disabled_rejected",
           post({"model": MODEL, "messages": [msg("hello")], "stream": True}, auth("conf-4")),
           400, "streaming_disabled")

    # Admission: too many messages.
    record("too_many_messages_rejected",
           post({"model": MODEL, "messages": [msg("a"), msg("b"), msg("c"), msg("d"), msg("e")]}, auth("conf-5")),
           400, "too_many_messages")

    # Admission: completion-token cap.
    record("max_tokens_too_large_rejected",
           post({"model": MODEL, "messages": [msg("hello")], "max_tokens": 4096}, auth("conf-6")),
           400, "max_tokens_too_large")

    # R-budget / T8 blast-radius cap: second request in a sandbox window is throttled.
    first = post({"model": MODEL, "messages": [msg("one")]}, auth("budget-test"))
    second = post({"model": MODEL, "messages": [msg("two")]}, auth("budget-test"))
    checks.append({
        "check": "sandbox_budget_throttles",
        "expected_status": 429,
        "actual_status": second.status_code,
        "expected_reason": "sandbox_request_budget_exceeded",
        "actual_reason": reason_of(second),
        "first_request_status": first.status_code,
        "passed": first.status_code == 200 and second.status_code == 429
                  and reason_of(second) == "sandbox_request_budget_exceeded",
    })

    return checks


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    mock = subprocess.Popen(
        [PYTHON, str(COC / "mock_runtime.py"), "--port", str(MOCK_PORT), "--delay-ms", "0"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    env = dict(os.environ)
    env.update(GATEWAY_ENV)
    gw_log = open(RESULTS / "conformance-gateway.log", "w")  # noqa: SIM115
    gw = subprocess.Popen(
        [PYTHON, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(GW_PORT),
         "--log-level", "info", "--no-access-log"],
        cwd=str(GATEWAY_DIR), env=env, stdout=gw_log, stderr=gw_log,
    )
    try:
        wait_healthy(MOCK_URL)
        wait_healthy(GW_URL)
        checks = run_checks()
    finally:
        for proc in (gw, mock):
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
        gw_log.close()

    passed = sum(1 for c in checks if c["passed"])
    out = {
        "experiment": "conformance",
        "summary": {"total": len(checks), "passed": passed, "failed": len(checks) - passed},
        "checks": checks,
    }
    (RESULTS / "conformance-evidence.json").write_text(json.dumps(out, indent=2))
    width = max(len(c["check"]) for c in checks)
    for c in checks:
        flag = "PASS" if c["passed"] else "FAIL"
        print(f"  [{flag}] {c['check']:<{width}}  status={c['actual_status']} reason={c['actual_reason']}")
    print(f"\n{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
