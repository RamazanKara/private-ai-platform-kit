"""Regression tests for request-lifecycle and tenant-isolation hardening.

Each test pins down a failure that previously passed silently: a leaked concurrency slot,
a receipt recording "200 allowed" for a 500, a Redis outage surfacing as a bare 500, a
flat API key reading another tenant's batch files, and per-path metric label explosion.
"""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest
from app.batchstore import BatchStoreError
from app.key_records import KeyRecordError, KeyRecordSet
from app.main import create_app
from app.metrics import AUTH_FAILURES
from app.response_store import ResponseStoreError
from fastapi.testclient import TestClient

from tests.gateway_support import FakeRuntimeClient, _tool_settings

CHAT = {"messages": [{"role": "user", "content": "hi"}]}
COMPLETION = {"id": "x", "object": "chat.completion", "choices": [{"message": {"role": "assistant", "content": "ok"}}]}
FLAT_KEY = "flat-key-value"
BOUND_KEY = "bound-key-value"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _receipts(caplog):
    return [json.loads(r.message) for r in caplog.records if r.name == "ai_platform_ops_lab.audit"]


def _auth_settings(tmp_path, **overrides):
    records = tmp_path / "records.json"
    records.write_text(json.dumps({"records": [{"sha256": _sha256(BOUND_KEY), "sandbox": "team-a"}]}))
    return _tool_settings(
        api_key_auth_enabled=True,
        api_key_sha256s=(_sha256(FLAT_KEY),),
        api_key_records_path=records,
        **overrides,
    )


# --- concurrency slots ------------------------------------------------------------------


@pytest.mark.parametrize("stream", [False, True])
def test_concurrency_slot_is_released_after_success(stream):
    app = create_app(_tool_settings(max_concurrent_requests=2, allow_streaming=True))
    app.state.runtime_client = FakeRuntimeClient(
        response=COMPLETION, stream_chunks=[b'data: {"choices":[]}\n\n', b"data: [DONE]\n\n"]
    )
    client = TestClient(app)

    for _ in range(3):  # more requests than slots: a leak would shed the third
        response = client.post("/v1/chat/completions", json={**CHAT, "stream": stream})
        assert response.status_code == 200
        _ = response.content
    assert app.state.inflight == 0


def test_concurrency_slot_is_released_after_runtime_error():
    app = create_app(_tool_settings(max_concurrent_requests=1))
    app.state.runtime_client = FakeRuntimeClient(error=httpx.ConnectError("down"))
    client = TestClient(app)

    assert client.post("/v1/chat/completions", json=CHAT).status_code == 502
    assert client.post("/v1/chat/completions", json=CHAT).status_code == 502  # not 503: slot was freed
    assert app.state.inflight == 0


def test_concurrency_slot_is_released_after_an_unexpected_exception():
    app = create_app(_tool_settings(max_concurrent_requests=1))
    app.state.runtime_client = FakeRuntimeClient(error=RuntimeError("bug"))
    client = TestClient(app, raise_server_exceptions=False)

    assert client.post("/v1/chat/completions", json=CHAT).status_code == 500
    assert app.state.inflight == 0


# --- the receipt matches what the client received ----------------------------------------


def test_unexpected_exception_is_recorded_as_500_not_allowed(caplog):
    caplog.set_level("INFO", logger="ai_platform_ops_lab.audit")
    app = create_app(_tool_settings())
    app.state.runtime_client = FakeRuntimeClient(error=RuntimeError("bug"))
    client = TestClient(app, raise_server_exceptions=False)

    assert client.post("/v1/chat/completions", json=CHAT).status_code == 500
    receipt = _receipts(caplog)[-1]
    assert receipt["status_code"] == 500
    assert receipt["decision"] != "allowed"


# --- state-store outages are retryable 503s ---------------------------------------------


class _DownBatchStore:
    def __getattr__(self, name):
        def _raise(*args, **kwargs):
            raise BatchStoreError("batch metadata backend is unavailable")

        return _raise


class _DownResponseStore:
    backend = "redis"

    def create(self, record):
        raise ResponseStoreError("response store backend is unavailable")

    def get(self, tenant, response_id):
        raise ResponseStoreError("response store backend is unavailable")

    def delete(self, tenant, response_id):
        raise ResponseStoreError("response store backend is unavailable")


def test_batch_store_outage_is_a_retryable_503():
    app = create_app(
        _tool_settings(batch_api_enabled=True, batch_object_store_backend="memory", batch_store_backend="memory")
    )
    app.state.batch_store = _DownBatchStore()
    response = TestClient(app).get("/v1/batches")

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.json()["detail"]["reason"] == "state_backend_unavailable"


def test_response_store_outage_is_a_503_and_recorded_as_one(caplog):
    caplog.set_level("INFO", logger="ai_platform_ops_lab.audit")
    app = create_app(_tool_settings(responses_store_enabled=True))
    app.state.response_store = _DownResponseStore()
    app.state.runtime_client = FakeRuntimeClient(response=COMPLETION)
    client = TestClient(app)

    created = client.post("/v1/responses", json={"input": "hi", "store": True})
    assert created.status_code == 503
    assert created.json()["detail"]["reason"] == "state_backend_unavailable"
    assert _receipts(caplog)[-1]["status_code"] == 503
    assert client.get("/v1/responses/resp_x").status_code == 503


# --- tenant-scoped state requires a bound credential --------------------------------------


def test_flat_key_cannot_use_tenant_scoped_batch_state(tmp_path):
    app = create_app(
        _auth_settings(
            tmp_path, batch_api_enabled=True, batch_object_store_backend="memory", batch_store_backend="memory"
        )
    )
    client = TestClient(app)

    # A flat key names whatever sandbox it likes; it must not reach team-a's files.
    spoofed = client.get("/v1/files", headers={"X-API-Key": FLAT_KEY, "X-Sandbox-ID": "team-a"})
    assert spoofed.status_code == 403
    assert spoofed.json()["detail"]["reason"] == "sandbox_binding_required"

    bound = client.get("/v1/files", headers={"X-API-Key": BOUND_KEY})
    assert bound.status_code == 200


def test_flat_key_cannot_store_or_read_responses(tmp_path):
    app = create_app(_auth_settings(tmp_path, responses_store_enabled=True))
    app.state.runtime_client = FakeRuntimeClient(response=COMPLETION)
    client = TestClient(app)

    refused = client.post("/v1/responses", headers={"X-API-Key": FLAT_KEY}, json={"input": "hi", "store": True})
    assert refused.status_code == 403
    assert client.get("/v1/responses/resp_x", headers={"X-API-Key": FLAT_KEY}).status_code == 403

    stored = client.post("/v1/responses", headers={"X-API-Key": BOUND_KEY}, json={"input": "hi", "store": True})
    assert stored.status_code == 200
    response_id = stored.json()["id"]
    assert client.get(f"/v1/responses/{response_id}", headers={"X-API-Key": BOUND_KEY}).status_code == 200
    # Stateless use stays open to every authenticated caller.
    stateless = client.post("/v1/responses", headers={"X-API-Key": FLAT_KEY}, json={"input": "hi"})
    assert stateless.status_code == 200


def test_per_key_budget_requires_a_sandbox_binding(tmp_path):
    records = tmp_path / "records.json"
    records.write_text(json.dumps({"records": [{"sha256": _sha256("k"), "budget": {"requestLimit": 5}}]}))
    with pytest.raises(KeyRecordError, match="must also bind a sandbox"):
        KeyRecordSet.from_path(records)


# --- bounded metric labels ---------------------------------------------------------------


def test_auth_failure_metric_uses_the_route_template_not_the_raw_path(tmp_path):
    app = create_app(_auth_settings(tmp_path, batch_api_enabled=True, batch_object_store_backend="memory"))
    client = TestClient(app)

    client.get("/v1/files/file-abc123")
    client.get("/v1/no-such-route/random-1")

    assert AUTH_FAILURES.labels("/v1/files/{file_id}", "invalid_or_missing_api_key")._value.get() >= 1
    assert AUTH_FAILURES.labels("unmatched", "invalid_or_missing_api_key")._value.get() >= 1
    sampled_paths = {
        sample.labels["route"]
        for metric in AUTH_FAILURES.collect()
        for sample in metric.samples
        if "route" in sample.labels
    }
    assert "/v1/files/file-abc123" not in sampled_paths
    assert "/v1/no-such-route/random-1" not in sampled_paths
