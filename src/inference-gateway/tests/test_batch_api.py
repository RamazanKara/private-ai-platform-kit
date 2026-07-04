"""Tests for the OpenAI-compatible Files + Batch API routes (ADR 0011)."""

from __future__ import annotations

from app.main import create_app
from app.settings import Settings
from fastapi.testclient import TestClient

_TWO_LINE_JSONL = (
    b'{"custom_id":"a","method":"POST","url":"/v1/chat/completions","body":{"messages":[{"role":"user","content":"hi"}]}}\n'
    b'{"custom_id":"b","method":"POST","url":"/v1/chat/completions","body":{"messages":[{"role":"user","content":"yo"}]}}\n'
)


def _settings(**overrides):
    base = {
        "runtime_backend": "ollama",
        "ollama_base_url": "http://ollama:11434",
        "vllm_base_url": "http://vllm:8000",
        "model_id": "m",
        "request_timeout_seconds": 5,
        "batch_api_enabled": True,
        "batch_object_store_backend": "memory",
        "batch_store_backend": "memory",
    }
    base.update(overrides)
    return Settings(**base)


def _client(**overrides):
    return TestClient(create_app(_settings(**overrides)))


def _upload(client, content=_TWO_LINE_JSONL, headers=None):
    return client.post(
        "/v1/files",
        files={"file": ("in.jsonl", content, "application/jsonl")},
        data={"purpose": "batch"},
        headers=headers or {},
    )


def test_disabled_returns_404():
    client = TestClient(create_app(_settings(batch_api_enabled=False)))
    resp = _upload(client)
    assert resp.status_code == 404
    assert resp.json()["detail"]["reason"] == "batch_api_disabled"


def test_file_upload_get_list_content_delete_roundtrip():
    client = _client()
    up = _upload(client)
    assert up.status_code == 200
    file_obj = up.json()
    assert file_obj["object"] == "file"
    assert file_obj["purpose"] == "batch"
    assert file_obj["bytes"] == len(_TWO_LINE_JSONL)
    file_id = file_obj["id"]

    got = client.get(f"/v1/files/{file_id}")
    assert got.status_code == 200 and got.json()["id"] == file_id

    listed = client.get("/v1/files")
    assert listed.json()["object"] == "list"
    assert [f["id"] for f in listed.json()["data"]] == [file_id]

    content = client.get(f"/v1/files/{file_id}/content")
    assert content.status_code == 200
    assert content.content == _TWO_LINE_JSONL

    deleted = client.delete(f"/v1/files/{file_id}")
    assert deleted.json()["deleted"] is True
    assert client.get(f"/v1/files/{file_id}").status_code == 404


def test_file_upload_rejects_bad_purpose_empty_and_oversized():
    client = _client(batch_max_file_bytes=10)
    bad_purpose = client.post(
        "/v1/files", files={"file": ("x", b"data", "application/jsonl")}, data={"purpose": "fine-tune"}
    )
    assert bad_purpose.status_code == 400 and bad_purpose.json()["detail"]["reason"] == "invalid_purpose"

    empty = client.post("/v1/files", files={"file": ("x", b"   \n", "application/jsonl")}, data={"purpose": "batch"})
    assert empty.status_code == 400 and empty.json()["detail"]["reason"] == "empty_file"

    oversized = _upload(client)  # limit is 10 bytes
    assert oversized.status_code == 413 and oversized.json()["detail"]["reason"] == "file_too_large"


def test_file_tenant_isolation():
    client = _client()
    up = _upload(client, headers={"X-Sandbox-ID": "team-a"})
    file_id = up.json()["id"]
    # A different tenant cannot see or fetch team-a's file.
    assert client.get(f"/v1/files/{file_id}", headers={"X-Sandbox-ID": "team-b"}).status_code == 404
    assert client.get("/v1/files", headers={"X-Sandbox-ID": "team-b"}).json()["data"] == []
    # The owning tenant can.
    assert client.get(f"/v1/files/{file_id}", headers={"X-Sandbox-ID": "team-a"}).status_code == 200


def test_batch_create_get_cancel_and_list():
    client = _client()
    file_id = _upload(client).json()["id"]
    created = client.post(
        "/v1/batches",
        json={"input_file_id": file_id, "endpoint": "/v1/chat/completions", "metadata": {"k": "v"}},
    )
    assert created.status_code == 200
    batch = created.json()
    assert batch["object"] == "batch"
    assert batch["status"] == "validating"
    assert batch["request_counts"]["total"] == 2  # two lines in the input file
    assert batch["metadata"] == {"k": "v"}
    batch_id = batch["id"]

    assert client.get(f"/v1/batches/{batch_id}").json()["id"] == batch_id

    listed = client.get("/v1/batches")
    assert listed.json()["object"] == "list"
    assert listed.json()["data"][0]["id"] == batch_id

    cancelled = client.post(f"/v1/batches/{batch_id}/cancel")
    assert cancelled.json()["status"] == "cancelling"


def test_batch_create_validation_errors():
    client = _client()
    file_id = _upload(client).json()["id"]
    # Unknown input file.
    missing = client.post("/v1/batches", json={"input_file_id": "file-nope", "endpoint": "/v1/chat/completions"})
    assert missing.status_code == 404 and missing.json()["detail"]["reason"] == "input_file_not_found"
    # Non-batchable endpoint.
    bad_ep = client.post("/v1/batches", json={"input_file_id": file_id, "endpoint": "/v1/images/generations"})
    assert bad_ep.status_code == 400 and bad_ep.json()["detail"]["reason"] == "invalid_endpoint"
    # Bad completion window.
    bad_win = client.post(
        "/v1/batches",
        json={"input_file_id": file_id, "endpoint": "/v1/chat/completions", "completion_window": "soon"},
    )
    assert bad_win.status_code == 400 and bad_win.json()["detail"]["reason"] == "invalid_completion_window"


def test_get_missing_batch_and_file_404():
    client = _client()
    assert client.get("/v1/batches/batch-nope").status_code == 404
    assert client.post("/v1/batches/batch-nope/cancel").status_code == 404
    assert client.get("/v1/files/file-nope").status_code == 404
    assert client.get("/v1/files/file-nope/content").status_code == 404
