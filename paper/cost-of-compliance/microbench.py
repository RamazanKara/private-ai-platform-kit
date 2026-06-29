#!/usr/bin/env python3
"""Microbenchmark of the gateway governance controls in isolation.

The end-to-end experiment measures the system under load, where ordinary proxy I/O
(per-request upstream client construction, sockets, serialization) dominates and the
governance logic is a small delta on top. This script removes that confound: it calls
the gateway's real governance functions directly and times them with no network in the
path, giving the pure per-request CPU cost of each control in nanoseconds.

Run under the gateway virtualenv with the gateway package importable:
    GATEWAY_DIR=.../src/inference-gateway python microbench.py
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path

GATEWAY_DIR = Path(os.environ.get("GATEWAY_DIR", str(Path(__file__).resolve().parents[2] / "src" / "inference-gateway")))
sys.path.insert(0, str(GATEWAY_DIR))

from app.budget import InMemorySandboxBudgetTracker  # noqa: E402
from app.main import _payload_fingerprint  # noqa: E402
from app.policy import ModelRoutingPolicy  # noqa: E402
from app.settings import Settings  # noqa: E402

PROMPT = (
    "You are a platform assistant. Summarise, in two sentences, why a regulated "
    "organisation might run language models on its own Kubernetes cluster instead of "
    "calling a hosted commercial API, and what evidence an auditor would expect to see."
)
PAYLOAD = {
    "model": "bench-model",
    "messages": [
        {"role": "system", "content": "You are a careful, concise assistant."},
        {"role": "user", "content": PROMPT},
    ],
}
API_KEY = "cost-of-compliance-bench-key"
API_KEY_SHA256 = hashlib.sha256(API_KEY.encode("utf-8")).hexdigest()


def bench(fn, iters: int = 200_000, trials: int = 7) -> dict:
    """Time `fn` over several trials; report ns/op as the median trial mean plus spread."""
    # warm up
    for _ in range(min(iters, 20_000)):
        fn()
    per_trial = []
    for _ in range(trials):
        start = time.perf_counter_ns()
        for _ in range(iters):
            fn()
        elapsed = time.perf_counter_ns() - start
        per_trial.append(elapsed / iters)
    return {
        "ns_per_op": round(statistics.median(per_trial), 1),
        "ns_min": round(min(per_trial), 1),
        "ns_stdev": round(statistics.pstdev(per_trial), 1),
        "ops_per_sec": round(1e9 / statistics.median(per_trial)),
        "iters": iters,
        "trials": trials,
    }


def main() -> int:
    base = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://127.0.0.1:9099",
        vllm_base_url="http://127.0.0.1:8000",
        model_id="bench-model",
        request_timeout_seconds=30.0,
        allowed_models=("bench-model",),
        api_key_auth_enabled=True,
        api_key_sha256s=(API_KEY_SHA256,),
        prompt_secret_detection_enabled=True,
        sandbox_budget_enabled=True,
        sandbox_request_budget=10_000_000,
        sandbox_prompt_char_budget=10_000_000_000,
        sandbox_estimated_token_budget=10_000_000_000,
    )
    no_secret = Settings(
        runtime_backend="ollama",
        ollama_base_url="http://127.0.0.1:9099",
        vllm_base_url="http://127.0.0.1:8000",
        model_id="bench-model",
        request_timeout_seconds=30.0,
        allowed_models=("bench-model",),
        prompt_secret_detection_enabled=False,
    )
    routing = ModelRoutingPolicy.default(base)
    tracker = InMemorySandboxBudgetTracker(base)

    def auth_check() -> None:
        digest = hashlib.sha256(API_KEY.encode("utf-8")).hexdigest()
        _ = digest == base.api_key_sha256s[0]

    def admission_with_secret() -> None:
        base.validate_admission(PAYLOAD)

    def admission_no_secret() -> None:
        no_secret.validate_admission(PAYLOAD)

    def model_allowlist() -> None:
        base.validate_model("bench-model")

    def routing_resolve() -> None:
        routing.resolve("bench-model", "bench-model")

    def budget_reserve() -> None:
        tracker.reserve("bench", PAYLOAD, base)

    def audit_fingerprint() -> None:
        _payload_fingerprint(PAYLOAD)

    def audit_serialize() -> None:
        event = {
            "event": "inference_request",
            "request_id": "coc-bench",
            "sandbox_id": "bench",
            "backend": "ollama",
            "model": "bench-model",
            "status_code": 200,
            "latency_ms": 12.3,
        }
        event.update(_payload_fingerprint(PAYLOAD))
        json.dumps(event, sort_keys=True)

    controls = {
        "auth_api_key": auth_check,
        "admission_with_secret_detection": admission_with_secret,
        "admission_without_secret_detection": admission_no_secret,
        "model_allowlist": model_allowlist,
        "model_routing_resolve": routing_resolve,
        "budget_reserve": budget_reserve,
        "audit_fingerprint": audit_fingerprint,
        "audit_fingerprint_and_serialize": audit_serialize,
    }

    results = {name: bench(fn) for name, fn in controls.items()}

    # Approximate the full per-request governance CPU cost: auth + routing + admission
    # (which already includes secret detection and the allowlist) + budget + audit.
    full_ns = (
        results["auth_api_key"]["ns_per_op"]
        + results["model_routing_resolve"]["ns_per_op"]
        + results["admission_with_secret_detection"]["ns_per_op"]
        + results["budget_reserve"]["ns_per_op"]
        + results["audit_fingerprint_and_serialize"]["ns_per_op"]
    )

    info = {"python": sys.version.split()[0]}
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        models = [
            line.split(":", 1)[1].strip()
            for line in cpuinfo.read_text().splitlines()
            if line.lower().startswith("model name")
        ]
        if models:
            info["cpu_model"] = models[0]
            info["cpu_threads"] = len(models)

    out = {
        "experiment": "governance-microbenchmark",
        "system": info,
        "prompt_chars": len(PROMPT),
        "controls": results,
        "full_governance_ns_per_request": round(full_ns, 1),
        "full_governance_us_per_request": round(full_ns / 1000.0, 3),
    }
    out_dir = Path(__file__).resolve().parent / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "micro-results.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
