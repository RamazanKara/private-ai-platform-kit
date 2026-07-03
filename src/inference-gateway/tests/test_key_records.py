"""Tests for optional API-key records (4.1) and usage/budget tenant scoping (4.2)."""

from __future__ import annotations

import hashlib
import json
import logging
import time

import pytest
from app.key_records import KeyRecordError, KeyRecordSet
from app.main import create_app
from app.settings import Settings
from fastapi.testclient import TestClient

# A distinct key per scenario. Digests are computed in-test from these plaintexts; the
# gateway matches API_KEY_RECORDS by the same sha256, so nothing here hardcodes a hash.
KEY_ALPHA = "key-alpha-value"
KEY_BETA = "key-beta-value"
KEY_FLAT = "key-flat-value"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class _FakeRuntimeClient:
    def __init__(self, response=None):
        self.response = response or {"id": "x", "object": "chat.completion", "choices": []}
        self.calls = 0

    async def chat_completions(self, payload, headers=None, backend=None):
        self.calls += 1
        return self.response

    async def health(self, backend=None):
        return {"status": "ok", "backend": backend}


def _write_records(tmp_path, records, *, suffix=".json"):
    path = tmp_path / f"key-records{suffix}"
    if suffix == ".json":
        path.write_text(json.dumps({"records": records}), encoding="utf-8")
    else:
        import yaml

        path.write_text(yaml.safe_dump({"records": records}), encoding="utf-8")
    return path


def _settings(tmp_path, records, **overrides):
    base = {
        "runtime_backend": "ollama",
        "ollama_base_url": "http://ollama:11434",
        "vllm_base_url": "http://vllm:8000",
        "model_id": "default-model",
        "request_timeout_seconds": 5,
        "api_key_auth_enabled": True,
        # Records-only auth (no flat hash) unless a test adds one, so the record path is
        # exercised directly.
        "api_key_records_path": _write_records(tmp_path, records) if records is not None else None,
    }
    base.update(overrides)
    return Settings(**base)


def _app(settings, response=None):
    app = create_app(settings)
    app.state.runtime_client = _FakeRuntimeClient(response=response)
    return app


# --- KeyRecordSet unit tests -------------------------------------------------


def test_empty_when_no_path():
    assert KeyRecordSet.from_path(None).records == ()


def test_loads_and_matches_record(tmp_path):
    path = _write_records(tmp_path, [{"sha256": _sha256(KEY_ALPHA), "name": "alpha", "scopes": ["chat:write"]}])
    record_set = KeyRecordSet.from_path(path)
    matched = record_set.match(_sha256(KEY_ALPHA))
    assert matched is not None
    assert matched.key_id == "alpha"
    assert matched.scopes == ("chat:write",)
    assert record_set.match(_sha256("nope")) is None


def test_loads_yaml_records(tmp_path):
    path = _write_records(tmp_path, [{"sha256": _sha256(KEY_ALPHA), "sandbox": "team-a"}], suffix=".yaml")
    record_set = KeyRecordSet.from_path(path)
    matched = record_set.match(_sha256(KEY_ALPHA))
    assert matched is not None
    assert matched.sandbox == "team-a"
    # No name -> key_id defaults to the 12-char digest prefix.
    assert matched.key_id == _sha256(KEY_ALPHA)[:12]


def test_key_id_defaults_to_digest_prefix(tmp_path):
    path = _write_records(tmp_path, [{"sha256": _sha256(KEY_ALPHA)}])
    matched = KeyRecordSet.from_path(path).match(_sha256(KEY_ALPHA))
    assert matched.key_id == _sha256(KEY_ALPHA)[:12]


def test_expires_at_iso8601_parses(tmp_path):
    path = _write_records(tmp_path, [{"sha256": _sha256(KEY_ALPHA), "expires_at": "2020-01-01T00:00:00Z"}])
    matched = KeyRecordSet.from_path(path).match(_sha256(KEY_ALPHA))
    assert matched.is_expired(time.time()) is True


def test_expires_at_epoch_future_not_expired(tmp_path):
    path = _write_records(tmp_path, [{"sha256": _sha256(KEY_ALPHA), "expires_at": int(time.time()) + 3600}])
    matched = KeyRecordSet.from_path(path).match(_sha256(KEY_ALPHA))
    assert matched.is_expired(time.time()) is False


def test_malformed_file_fails_closed(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{ this is : not valid json ][", encoding="utf-8")
    with pytest.raises(KeyRecordError):
        KeyRecordSet.from_path(path)


def test_missing_sha256_fails_closed(tmp_path):
    path = _write_records(tmp_path, [{"name": "no-hash"}])
    with pytest.raises(KeyRecordError, match="sha256"):
        KeyRecordSet.from_path(path)


def test_invalid_sha256_fails_closed(tmp_path):
    path = _write_records(tmp_path, [{"sha256": "tooshort"}])
    with pytest.raises(KeyRecordError, match="sha256"):
        KeyRecordSet.from_path(path)


def test_duplicate_digest_fails_closed(tmp_path):
    digest = _sha256(KEY_ALPHA)
    path = _write_records(tmp_path, [{"sha256": digest}, {"sha256": digest}])
    with pytest.raises(KeyRecordError, match="duplicate"):
        KeyRecordSet.from_path(path)


def test_bad_expires_at_fails_closed(tmp_path):
    path = _write_records(tmp_path, [{"sha256": _sha256(KEY_ALPHA), "expires_at": "not-a-date"}])
    with pytest.raises(KeyRecordError, match="expires_at"):
        KeyRecordSet.from_path(path)


def test_invalid_sandbox_fails_closed(tmp_path):
    path = _write_records(tmp_path, [{"sha256": _sha256(KEY_ALPHA), "sandbox": "Not A Valid Sandbox"}])
    with pytest.raises(KeyRecordError, match="sandbox"):
        KeyRecordSet.from_path(path)


# --- Auth path integration tests (4.1) ---------------------------------------


def test_scoped_key_is_accepted(tmp_path):
    settings = _settings(tmp_path, [{"sha256": _sha256(KEY_ALPHA), "name": "alpha", "scopes": ["chat:write"]}])
    client = TestClient(_app(settings))
    response = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": KEY_ALPHA},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200


def test_expired_key_is_rejected(tmp_path):
    settings = _settings(
        tmp_path,
        [{"sha256": _sha256(KEY_ALPHA), "expires_at": int(time.time()) - 10}],
    )
    client = TestClient(_app(settings))
    response = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": KEY_ALPHA},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["reason"] == "api_key_expired"


def test_flat_key_still_works_alongside_records(tmp_path):
    settings = _settings(
        tmp_path,
        [{"sha256": _sha256(KEY_ALPHA), "sandbox": "team-a"}],
        api_key_sha256s=(_sha256(KEY_FLAT),),
    )
    client = TestClient(_app(settings))
    # Flat key: unbound, so it may assert any sandbox via the header.
    response = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": KEY_FLAT, "X-Sandbox-ID": "any-team"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    assert response.headers["X-Sandbox-ID"] == "any-team"


def test_sandbox_bound_key_adopts_binding_without_header(tmp_path):
    settings = _settings(tmp_path, [{"sha256": _sha256(KEY_ALPHA), "sandbox": "team-a"}])
    client = TestClient(_app(settings))
    response = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": KEY_ALPHA},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    assert response.headers["X-Sandbox-ID"] == "team-a"


def test_sandbox_bound_key_cannot_act_as_another_sandbox(tmp_path):
    settings = _settings(tmp_path, [{"sha256": _sha256(KEY_ALPHA), "sandbox": "team-a"}])
    client = TestClient(_app(settings))
    response = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": KEY_ALPHA, "X-Sandbox-ID": "team-b"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "sandbox_identity_mismatch"


def test_sandbox_bound_key_matching_header_is_accepted(tmp_path):
    settings = _settings(tmp_path, [{"sha256": _sha256(KEY_ALPHA), "sandbox": "team-a"}])
    client = TestClient(_app(settings))
    response = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": KEY_ALPHA, "X-Sandbox-ID": "team-a"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200


def test_record_principal_is_recorded_in_audit(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="ai_platform_ops_lab.audit")
    settings = _settings(
        tmp_path,
        [{"sha256": _sha256(KEY_ALPHA), "name": "team-a-key", "sandbox": "team-a", "scopes": ["chat:write"]}],
    )
    client = TestClient(_app(settings))
    response = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": KEY_ALPHA},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    audit = [r.getMessage() for r in caplog.records if '"event": "inference_request"' in r.getMessage()][-1]
    event = json.loads(audit)
    assert event["principal"]["auth"] == "api_key"
    assert event["principal"]["key_id"] == "team-a-key"
    assert event["principal"]["bound_sandbox"] == "team-a"
    assert "chat:write" in event["principal"]["scopes"]


def test_per_key_request_budget_override_takes_effect(tmp_path):
    # A record caps this key's request budget at 1; the sandbox default is generous.
    settings = _settings(
        tmp_path,
        [{"sha256": _sha256(KEY_ALPHA), "sandbox": "team-a", "budget": {"requestLimit": 1}}],
        sandbox_budget_enabled=True,
        sandbox_request_budget=1000,
    )
    client = TestClient(_app(settings))
    first = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": KEY_ALPHA},
        json={"messages": [{"role": "user", "content": "one"}]},
    )
    second = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": KEY_ALPHA},
        json={"messages": [{"role": "user", "content": "two"}]},
    )
    assert first.status_code == 200
    # Second request exceeds the per-key request budget of 1 (429), proving the override
    # applied instead of the sandbox default of 1000.
    assert second.status_code == 429
    assert second.json()["detail"]["reason"] == "sandbox_request_budget_exceeded"


def test_per_key_budget_override_reflected_in_usage(tmp_path):
    settings = _settings(
        tmp_path,
        [{"sha256": _sha256(KEY_ALPHA), "sandbox": "team-a", "budget": {"estimatedTokenLimit": 42}}],
        sandbox_budget_enabled=True,
        sandbox_estimated_token_budget=999999,
    )
    client = TestClient(_app(settings))
    usage = client.get("/v1/usage", headers={"X-API-Key": KEY_ALPHA})
    assert usage.status_code == 200
    # The per-key override, not the sandbox default, is the limit surfaced to the caller.
    assert usage.json()["limits"]["estimated_tokens"] == 42


# --- Usage/budget tenant scoping (4.2) ---------------------------------------


def test_bound_key_usage_is_scoped_to_own_sandbox(tmp_path):
    # A caller bound to team-a requesting team-b's usage via the header is rejected: the
    # binding check runs in middleware for the GET routes too.
    settings = _settings(tmp_path, [{"sha256": _sha256(KEY_ALPHA), "sandbox": "team-a"}])
    client = TestClient(_app(settings))
    other = client.get("/v1/usage", headers={"X-API-Key": KEY_ALPHA, "X-Sandbox-ID": "team-b"})
    own = client.get("/v1/usage", headers={"X-API-Key": KEY_ALPHA, "X-Sandbox-ID": "team-a"})
    own_no_header = client.get("/v1/usage", headers={"X-API-Key": KEY_ALPHA})
    assert other.status_code == 403
    assert other.json()["detail"]["reason"] == "sandbox_identity_mismatch"
    assert own.status_code == 200
    assert own.json()["sandbox_id"] == "team-a"
    # Without a header the bound sandbox is adopted, never the default.
    assert own_no_header.status_code == 200
    assert own_no_header.json()["sandbox_id"] == "team-a"


def test_bound_key_budget_is_scoped_to_own_sandbox(tmp_path):
    settings = _settings(tmp_path, [{"sha256": _sha256(KEY_ALPHA), "sandbox": "team-a"}])
    client = TestClient(_app(settings))
    other = client.get("/v1/sandbox/budget", headers={"X-API-Key": KEY_ALPHA, "X-Sandbox-ID": "team-b"})
    own = client.get("/v1/sandbox/budget", headers={"X-API-Key": KEY_ALPHA})
    assert other.status_code == 403
    assert own.status_code == 200
    assert own.json()["sandbox_id"] == "team-a"


def test_unbound_flat_key_usage_is_header_trusted(tmp_path):
    # Documented insecure default: with no binding a flat key reads any sandbox's usage
    # via the header. This test pins that behavior so the binding change did not alter it.
    settings = _settings(tmp_path, None, api_key_sha256s=(_sha256(KEY_FLAT),))
    client = TestClient(_app(settings))
    response = client.get("/v1/usage", headers={"X-API-Key": KEY_FLAT, "X-Sandbox-ID": "some-other-team"})
    assert response.status_code == 200
    assert response.json()["sandbox_id"] == "some-other-team"


# --- Records take precedence over the flat allowlist (review finding) --------


def test_record_binding_takes_precedence_over_flat_listing(tmp_path):
    # A key listed in BOTH the flat allowlist and a binding record must still be governed
    # by the record's sandbox binding - the flat entry cannot silently void it.
    records = [{"sha256": _sha256(KEY_ALPHA), "name": "team-a-key", "sandbox": "team-a"}]
    settings = _settings(tmp_path, records, api_key_sha256s=(_sha256(KEY_ALPHA),))
    client = TestClient(_app(settings))

    resp = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": KEY_ALPHA, "X-Sandbox-ID": "team-b"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "sandbox_identity_mismatch"


def test_expired_record_takes_precedence_over_flat_listing(tmp_path):
    # An expired record must reject even when the same digest is flat-listed (which alone
    # would authenticate as an unbound principal).
    records = [{"sha256": _sha256(KEY_ALPHA), "name": "team-a-key", "expires_at": 1}]
    settings = _settings(tmp_path, records, api_key_sha256s=(_sha256(KEY_ALPHA),))
    client = TestClient(_app(settings))

    resp = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": KEY_ALPHA, "X-Sandbox-ID": "team-a"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "api_key_expired"
