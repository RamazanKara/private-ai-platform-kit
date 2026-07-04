"""Tests for the operator audit-chain verifier (scripts/audit-verify.py).

These exercise the verifier against a real gateway-emitted, double-logged audit stream:
that the double-logged copies are deduplicated, a clean chain verifies, a tampered record is
detected, and — critically — that the verifier's replicated canonicalization agrees byte for
byte with the gateway's live ``_chain_audit_event`` output. Keeping the two in lockstep is
the maintenance obligation ADR 0006 calls out; this test fails the quality gate if they drift.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import sys
from pathlib import Path

from app.main import create_app
from app.settings import Settings
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]


def _load_verifier():
    path = ROOT / "scripts" / "audit-verify.py"
    spec = importlib.util.spec_from_file_location("audit_verify", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_verify"] = module
    spec.loader.exec_module(module)
    return module


def _tool_settings(**overrides):
    base = {
        "runtime_backend": "vllm",
        "ollama_base_url": "http://ollama:11434",
        "vllm_base_url": "http://vllm:8000",
        "model_id": "default-model",
        "request_timeout_seconds": 5,
    }
    base.update(overrides)
    return Settings(**base)


class _FakeRuntime:
    # Return no usage so these tests stay neutral to the process-global Prometheus token
    # counters (other tests assert exact token deltas); the verifier ignores usage anyway.
    async def chat_completions(self, payload, headers=None, backend=None):
        return {"id": "x", "object": "chat.completion", "choices": []}

    async def aclose(self):
        return None


def _double_logged_lines(caplog) -> list[str]:
    """The pod log carries every audit record twice (audit logger + uvicorn.error)."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.name in ("ai_platform_ops_lab.audit", "uvicorn.error") and "record_hash" in r.getMessage()
    ]


def _emit_stream(caplog):
    caplog.set_level(logging.INFO)
    app = create_app(_tool_settings())
    app.state.runtime_client = _FakeRuntime()
    client = TestClient(app)
    for i in range(3):
        client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": f"hi {i}"}]})
    client.post(
        "/v1/batch-inference",
        json={
            "requests": [
                {"messages": [{"role": "user", "content": "a"}]},
                {"messages": [{"role": "user", "content": "b"}]},
            ]
        },
    )
    return app, _double_logged_lines(caplog)


def test_verifier_dedups_and_verifies_live_gateway_chain(caplog):
    verifier = _load_verifier()
    app, lines = _emit_stream(caplog)

    # Every record appears twice in the pod log; dedup by record_hash halves the count.
    events = verifier.extract_audit_events(lines)
    deduped = verifier.deduplicate(events)
    assert len(events) == 2 * len(deduped)
    assert len(deduped) == 4  # three inference_request + one batch_request

    chains = verifier.group_into_chains(deduped)
    assert len(chains) == 1
    assert chains[0].chain_id == app.state.audit_chain_id
    result = verifier.verify_chain(chains[0])
    assert result.ok
    assert result.count == 4


def test_verifier_detects_tampered_record(caplog):
    verifier = _load_verifier()
    _app, lines = _emit_stream(caplog)

    # Edit one hash-covered field, leaving the stored record_hash: must be detected.
    tampered = []
    edited = False
    for line in lines:
        brace = line.find("{")
        obj = json.loads(line[brace:])
        if obj.get("event") == "inference_request" and not edited:
            obj["status_code"] = 500
            edited = True
        tampered.append(json.dumps(obj, sort_keys=True))
    assert edited

    chains = verifier.group_into_chains(verifier.deduplicate(verifier.extract_audit_events(tampered)))
    result = verifier.verify_chain(chains[0])
    assert not result.ok
    assert result.reason == "record_hash_mismatch"


def test_verifier_canonicalization_matches_gateway(caplog):
    """The verifier's replicated hashing reproduces the gateway's live record_hash exactly."""
    verifier = _load_verifier()
    _app, lines = _emit_stream(caplog)

    genesis = hashlib.sha256(b"genesis").hexdigest()
    assert genesis == verifier.GENESIS
    chains = verifier.group_into_chains(verifier.deduplicate(verifier.extract_audit_events(lines)))
    prev = genesis
    for record in chains[0].records:
        payload = {k: v for k, v in record.items() if k not in ("prev_hash", "record_hash")}
        assert verifier.compute_record_hash(prev, payload) == record["record_hash"]
        prev = record["record_hash"]


def test_verifier_selftest_passes():
    verifier = _load_verifier()
    assert verifier.selftest() == 0


def test_verifier_detects_tampered_duplicate_copy(caplog):
    # If an attacker tampers ONE of the two logged copies and leaves the other clean,
    # dedup-by-record_hash must not hide it: two copies sharing a record_hash but differing
    # in content is itself proof of tampering.
    verifier = _load_verifier()
    _app, lines = _emit_stream(caplog)

    tampered = []
    edited = False
    for line in lines:
        brace = line.find("{")
        obj = json.loads(line[brace:])
        if obj.get("event") == "inference_request" and "sandbox_id" in obj and not edited:
            obj["sandbox_id"] = obj["sandbox_id"] + "-EVIL"  # leave record_hash intact
            edited = True
            tampered.append(json.dumps(obj))
        else:
            tampered.append(line[brace:] if brace >= 0 else line)
    assert edited

    conflicts = verifier.divergent_duplicates(verifier.extract_audit_events(tampered))
    assert conflicts, "a tampered duplicate copy must be detected, not deduplicated away"
