#!/usr/bin/env python3
"""Cost-of-compliance experiment orchestrator.

Stands up the mock runtime and the inference gateway under controlled configurations,
drives closed-loop load against each, and records the latency/throughput distribution.

Configurations
--------------
  direct        client -> mock runtime (no gateway): the baseline.
  gw-min        gateway with governance features off (proxy + routing + admission only).
  gw-auth       gw-min + API-key authentication.
  gw-audit      gw-min + redacted, fingerprinted audit logging.
  gw-secret     gw-min + prompt secret detection.
  gw-allowlist  gw-min + model allowlist enforcement.
  gw-budget     gw-min + per-sandbox budget enforcement.
  gw-full       all governance features on.

Run under the gateway's virtualenv interpreter so FastAPI/uvicorn/httpx resolve.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import httpx

import bench

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
GATEWAY_DIR = Path(os.environ.get("GATEWAY_DIR", str(Path(__file__).resolve().parents[2] / "src" / "inference-gateway")))
PYTHON = sys.executable

MOCK_PORT = int(os.environ.get("MOCK_PORT", "9099"))
GW_PORT = int(os.environ.get("GW_PORT", "8099"))
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"
GW_URL = f"http://127.0.0.1:{GW_PORT}"

API_KEY = "cost-of-compliance-bench-key"
API_KEY_SHA256 = hashlib.sha256(API_KEY.encode("utf-8")).hexdigest()

WARMUP = float(os.environ.get("WARMUP", "2.0"))
DURATION = float(os.environ.get("DURATION", "8.0"))
REPEATS = int(os.environ.get("REPEATS", "5"))
SMOKE = os.environ.get("SMOKE") == "1"
PHASE = os.environ.get("PHASE", "")

BASE_ENV = {
    "RUNTIME_BACKEND": "ollama",
    "OLLAMA_BASE_URL": MOCK_URL,
    "MODEL_ID": "bench-model",
    "ALLOW_STREAMING": "false",
    "REQUEST_TIMEOUT_SECONDS": "30",
    "OTEL_TRACING_ENABLED": "false",
}

HIGH_BUDGET = {
    "SANDBOX_BUDGET_ENABLED": "true",
    "SANDBOX_REQUEST_BUDGET": "100000000",
    "SANDBOX_PROMPT_CHAR_BUDGET": "100000000000",
    "SANDBOX_ESTIMATED_TOKEN_BUDGET": "100000000000",
    "SANDBOX_BUDGET_WINDOW_SECONDS": "86400",
    "SANDBOX_BUDGET_BACKEND": "memory",
}

OFF = {
    "API_KEY_AUTH_ENABLED": "false",
    "JWT_AUTH_ENABLED": "false",
    "AUDIT_LOG_ENABLED": "false",
    "PROMPT_SECRET_DETECTION_ENABLED": "false",
    "SANDBOX_BUDGET_ENABLED": "false",
    "ALLOWED_MODELS": "",
}


def gateway_env(config: str, mock_url: str = MOCK_URL) -> dict[str, str]:
    env = dict(BASE_ENV)
    env["OLLAMA_BASE_URL"] = mock_url
    env.update(OFF)
    if config == "gw-min":
        pass
    elif config == "gw-auth":
        env.update({"API_KEY_AUTH_ENABLED": "true", "API_KEY_SHA256S": API_KEY_SHA256})
    elif config == "gw-audit":
        env.update({"AUDIT_LOG_ENABLED": "true"})
    elif config == "gw-secret":
        env.update({"PROMPT_SECRET_DETECTION_ENABLED": "true"})
    elif config == "gw-allowlist":
        env.update({"ALLOWED_MODELS": "bench-model"})
    elif config == "gw-budget":
        env.update(HIGH_BUDGET)
    elif config == "gw-full":
        env.update(
            {
                "API_KEY_AUTH_ENABLED": "true",
                "API_KEY_SHA256S": API_KEY_SHA256,
                "AUDIT_LOG_ENABLED": "true",
                "PROMPT_SECRET_DETECTION_ENABLED": "true",
                "ALLOWED_MODELS": "bench-model",
            }
        )
        env.update(HIGH_BUDGET)
    else:
        raise ValueError(f"unknown config {config}")
    return env


def wait_healthy(url: str, timeout: float = 25.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            r = httpx.get(f"{url}/healthz", timeout=2.0)
            if r.status_code == 200:
                return
            last = r.status_code
        except Exception as exc:  # noqa: BLE001
            last = type(exc).__name__
        time.sleep(0.25)
    raise RuntimeError(f"service at {url} not healthy (last={last})")


def start_mock(delay_ms: float, port: int = MOCK_PORT) -> subprocess.Popen:
    # A unique port per delay group: even if a previous mock lingers, the new mock
    # never collides with it, so the configured think-time is always the one in effect.
    proc = subprocess.Popen(
        [PYTHON, str(HERE / "mock_runtime.py"), "--port", str(port), "--delay-ms", str(delay_ms)],
        cwd=str(HERE),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    wait_healthy(f"http://127.0.0.1:{port}")
    return proc


def start_gateway(config: str, logfile: Path, mock_url: str = MOCK_URL) -> subprocess.Popen:
    env = dict(os.environ)
    env.update(gateway_env(config, mock_url))
    handle = open(logfile, "w")  # noqa: SIM115  (kept open for the gateway's lifetime)
    proc = subprocess.Popen(
        [
            PYTHON, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(GW_PORT),
            "--log-level", "info", "--no-access-log",
        ],
        cwd=str(GATEWAY_DIR),
        env=env,
        stdout=handle,
        stderr=handle,
    )
    proc._coc_log = handle  # type: ignore[attr-defined]
    wait_healthy(GW_URL)
    return proc


def stop(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    handle = getattr(proc, "_coc_log", None)
    if handle is not None:
        handle.close()


def headers_for(config: str) -> dict[str, str]:
    if config in ("gw-auth", "gw-full"):
        return {"X-API-Key": API_KEY}
    return {}


MAX_CLIENT_PROCS = int(os.environ.get("MAX_CLIENT_PROCS", "8"))


def _shard_concurrency(concurrency: int) -> list[int]:
    """Split target concurrency across at most MAX_CLIENT_PROCS client processes."""
    procs = min(concurrency, MAX_CLIENT_PROCS)
    base, extra = divmod(concurrency, procs)
    return [base + (1 if i < extra else 0) for i in range(procs)]


def run_sharded(target_url: str, concurrency: int, config: str, warmup: float, duration: float) -> dict:
    """Generate load with multiple client processes and aggregate the results.

    A single asyncio client saturates one core well below the gateway's ceiling, so
    offered concurrency is spread across processes; latencies are pooled and
    throughput summed to recover the true server-side distribution.
    """
    shards = _shard_concurrency(concurrency)
    procs: list[subprocess.Popen] = []
    for vus in shards:
        cmd = [
            PYTHON, str(HERE / "bench.py"),
            "--url", target_url,
            "--concurrency", str(vus),
            "--warmup", str(warmup),
            "--duration", str(duration),
            "--raw",
        ]
        if config in ("gw-auth", "gw-full"):
            cmd += ["--api-key", API_KEY]
        procs.append(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL))

    latencies: list[float] = []
    requests = 0
    errors = 0
    for proc in procs:
        out, _ = proc.communicate()
        shard = json.loads(out.decode("utf-8"))
        latencies.extend(shard.get("_latencies", []))
        requests += shard["requests"]
        errors += shard["errors"]

    ok = len(latencies)
    return {
        "requests": requests,
        "errors": errors,
        "error_rate": (errors / requests) if requests else 0.0,
        "throughput_rps": round(ok / duration, 3),
        "client_processes": len(shards),
        "latency_ms": {
            "p50": round(bench._percentile(latencies, 50), 3),
            "p90": round(bench._percentile(latencies, 90), 3),
            "p95": round(bench._percentile(latencies, 95), 3),
            "p99": round(bench._percentile(latencies, 99), 3),
            "mean": round(sum(latencies) / ok, 3) if ok else 0.0,
            "min": round(min(latencies), 3) if ok else 0.0,
            "max": round(max(latencies), 3) if ok else 0.0,
        },
    }


def build_matrix() -> list[dict]:
    """Return the list of (delay, config, concurrencies) cells to run."""
    if SMOKE:
        return [
            {"delay_ms": 0, "config": "direct", "concurrency": [4]},
            {"delay_ms": 0, "config": "gw-min", "concurrency": [4]},
            {"delay_ms": 0, "config": "gw-full", "concurrency": [4]},
        ]

    context_conc = [int(os.environ.get("CONTEXT_CONC", "1"))]
    if PHASE == "delay":
        # Phase 3 only: governance overhead as a fraction of inference think-time.
        cells = []
        for delay in (0, 50, 200, 500):
            cells.append({"delay_ms": delay, "config": "direct", "concurrency": context_conc})
            cells.append({"delay_ms": delay, "config": "gw-min", "concurrency": context_conc})
            cells.append({"delay_ms": delay, "config": "gw-full", "concurrency": context_conc})
        return cells

    headline_conc = [1, 2, 4, 8, 16, 32]
    attrib_conc = [1, 8]
    cells: list[dict] = []
    # Phase 1: baseline, ungoverned gateway, fully governed gateway across a sweep.
    cells.append({"delay_ms": 0, "config": "direct", "concurrency": headline_conc})
    cells.append({"delay_ms": 0, "config": "gw-min", "concurrency": headline_conc})
    cells.append({"delay_ms": 0, "config": "gw-full", "concurrency": headline_conc})
    # Phase 2: per-control attribution (delta over gw-min).
    for cfg in ("gw-auth", "gw-audit", "gw-secret", "gw-allowlist", "gw-budget"):
        cells.append({"delay_ms": 0, "config": cfg, "concurrency": attrib_conc})
    # Phase 3: governance overhead as a fraction of realistic inference latency.
    for delay in (50, 200, 500):
        cells.append({"delay_ms": delay, "config": "direct", "concurrency": context_conc})
        cells.append({"delay_ms": delay, "config": "gw-min", "concurrency": context_conc})
        cells.append({"delay_ms": delay, "config": "gw-full", "concurrency": context_conc})
    return cells


def system_info() -> dict:
    info = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor(),
    }
    try:
        import fastapi  # noqa: PLC0415

        info["fastapi"] = fastapi.__version__
    except Exception:  # noqa: BLE001
        info["fastapi"] = "unknown"
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
    return info


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    repeats = 2 if SMOKE else REPEATS
    warmup = 1.0 if SMOKE else WARMUP
    duration = 3.0 if SMOKE else DURATION

    matrix = build_matrix()
    # Group cells by delay so the mock restarts only when think-time changes.
    by_delay: dict[float, list[dict]] = {}
    for cell in matrix:
        by_delay.setdefault(cell["delay_ms"], []).append(cell)

    runs: list[dict] = []
    started = time.time()
    for idx, delay_ms in enumerate(sorted(by_delay)):
        mock_port = MOCK_PORT + idx
        mock_url = f"http://127.0.0.1:{mock_port}"
        print(f"[delay={delay_ms}ms] starting mock runtime on :{mock_port}", flush=True)
        mock = start_mock(delay_ms, mock_port)
        try:
            for cell in by_delay[delay_ms]:
                config = cell["config"]
                gw = None
                target_url = mock_url
                if config != "direct":
                    logfile = RESULTS_DIR / f"gateway-{config}-d{int(delay_ms)}.log"
                    gw = start_gateway(config, logfile, mock_url)
                    target_url = GW_URL
                try:
                    for conc in cell["concurrency"]:
                        for rep in range(repeats):
                            metrics = run_sharded(
                                f"{target_url}/v1/chat/completions",
                                concurrency=conc,
                                config=config,
                                warmup=warmup,
                                duration=duration,
                            )
                            row = {
                                "config": config,
                                "delay_ms": delay_ms,
                                "concurrency": conc,
                                "repeat": rep,
                                **metrics,
                            }
                            runs.append(row)
                            lm = metrics["latency_ms"]
                            print(
                                f"  {config:>12} d={int(delay_ms):>3} c={conc:>3} r={rep} "
                                f"p50={lm['p50']:.2f} p95={lm['p95']:.2f} "
                                f"rps={metrics['throughput_rps']:.1f} err={metrics['errors']}",
                                flush=True,
                            )
                finally:
                    stop(gw)
        finally:
            stop(mock)

    out = {
        "experiment": "cost-of-compliance",
        "system": system_info(),
        "params": {"warmup_s": warmup, "duration_s": duration, "repeats": repeats, "smoke": SMOKE},
        "runs": runs,
        "wall_seconds": round(time.time() - started, 1),
    }
    out_name = "smoke.json" if SMOKE else ("delay-results.json" if PHASE == "delay" else "raw-results.json")
    out_path = RESULTS_DIR / out_name
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {out_path} ({len(runs)} runs, {out['wall_seconds']}s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
