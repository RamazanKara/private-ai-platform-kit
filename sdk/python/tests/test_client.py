"""Tests for the first-party Python SDK (retry/backoff, streaming, accessors).

All HTTP traffic goes through ``httpx.MockTransport`` and ``_sleep`` is replaced
with a recorder, so the suite is deterministic and never sleeps for real.
"""

import json

import ai_platform_client
import httpx
import pytest
from ai_platform_client import GatewayClient, GatewayError, GatewayRetryAfterError, GatewayStreamError


def _mock_transport(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(
        ai_platform_client.httpx,
        "Client",
        lambda *args, **kwargs: real_client(*args, transport=httpx.MockTransport(handler), **kwargs),
    )


def _record_sleeps(monkeypatch, client):
    sleeps: list[float] = []
    monkeypatch.setattr(client, "_sleep", sleeps.append)
    return sleeps


def test_retry_honors_retry_after_header(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"}, json={"error": "rate limited"})
        return httpx.Response(200, json={"choices": []})

    _mock_transport(monkeypatch, handler)
    client = GatewayClient("http://gateway.test")
    sleeps = _record_sleeps(monkeypatch, client)

    assert client.chat([{"role": "user", "content": "hi"}]) == {"choices": []}
    # Retry-After (3s) beats the first backoff step (0.25s).
    assert sleeps == [3.0]
    assert calls == ["/v1/chat/completions", "/v1/chat/completions"]


def test_retry_after_above_cap_fails_fast(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        # Budget-window 429s advertise the whole window (e.g. an hour).
        return httpx.Response(429, headers={"Retry-After": "3600"}, json={"error": "budget exceeded"})

    _mock_transport(monkeypatch, handler)
    client = GatewayClient("http://gateway.test", retry_after_cap=5.0)
    sleeps = _record_sleeps(monkeypatch, client)

    with pytest.raises(GatewayRetryAfterError) as excinfo:
        client.usage()
    assert isinstance(excinfo.value, httpx.HTTPStatusError)
    assert excinfo.value.retry_after == 3600
    assert calls == ["/v1/usage"]
    assert sleeps == []


def test_retry_after_equal_to_cap_still_sleeps_and_retries(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "5"}, json={"error": "rate limited"})
        return httpx.Response(200, json={"ok": True})

    _mock_transport(monkeypatch, handler)
    client = GatewayClient("http://gateway.test", retry_after_cap=5.0)
    sleeps = _record_sleeps(monkeypatch, client)

    assert client.usage() == {"ok": True}
    assert sleeps == [5.0]
    assert calls == ["/v1/usage", "/v1/usage"]


def test_retry_after_above_cap_on_last_attempt_fails_fast(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) <= 2:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "rate limited"})
        return httpx.Response(429, headers={"Retry-After": "6"}, json={"error": "budget exceeded"})

    _mock_transport(monkeypatch, handler)
    client = GatewayClient("http://gateway.test", max_retries=2, retry_after_cap=5.0)
    sleeps = _record_sleeps(monkeypatch, client)

    with pytest.raises(GatewayRetryAfterError) as excinfo:
        client.usage()
    assert excinfo.value.retry_after == 6
    assert calls == ["/v1/usage"] * 3
    assert sleeps == [2.0, 2.0]


@pytest.mark.parametrize("retry_after", ["soon", "-2", "1.5", ""])
def test_malformed_retry_after_falls_back_to_backoff(monkeypatch, retry_after):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(503, headers={"Retry-After": retry_after}, json={"error": "unavailable"})
        return httpx.Response(200, json={"ok": True})

    _mock_transport(monkeypatch, handler)
    client = GatewayClient("http://gateway.test")
    sleeps = _record_sleeps(monkeypatch, client)

    assert client.usage() == {"ok": True}
    assert sleeps == [0.25]


def test_absent_retry_after_uses_exponential_backoff(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) <= 2:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"ok": True})

    _mock_transport(monkeypatch, handler)
    client = GatewayClient("http://gateway.test")
    sleeps = _record_sleeps(monkeypatch, client)

    assert client.usage() == {"ok": True}
    assert sleeps == [0.25, 0.5]


def test_retry_exhaustion_raises_status_error(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "rate limited"})

    _mock_transport(monkeypatch, handler)
    client = GatewayClient("http://gateway.test", max_retries=2)
    sleeps = _record_sleeps(monkeypatch, client)

    with pytest.raises(httpx.HTTPStatusError):
        client.chat([{"role": "user", "content": "hi"}])
    assert len(calls) == 3
    assert sleeps == [2.0, 2.0]


def test_chat_stream_yields_content_deltas(monkeypatch):
    body = (
        b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        b'data: {"choices":[{"delta":{}}]}\n\n'
        b"data: [DONE]\n\n"
    )

    def handler(request):
        return httpx.Response(200, content=body)

    _mock_transport(monkeypatch, handler)
    with GatewayClient("http://gateway.test") as client:
        assert list(client.chat_stream([{"role": "user", "content": "hi"}])) == ["Hel", "lo"]


def test_chat_stream_raises_on_terminal_error_event(monkeypatch):
    body = (
        b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        b'data: {"error":{"message":"runtime unavailable","reason":"upstream_error"}}\n\n'
    )

    def handler(request):
        return httpx.Response(200, content=body)

    _mock_transport(monkeypatch, handler)
    with GatewayClient("http://gateway.test") as client:
        stream = client.chat_stream([{"role": "user", "content": "hi"}])
        assert next(stream) == "partial"
        with pytest.raises(GatewayStreamError) as excinfo:
            next(stream)
    assert excinfo.value.error["reason"] == "upstream_error"
    assert "runtime unavailable" in str(excinfo.value)


def test_sandbox_budget_hits_budget_path_with_platform_headers(monkeypatch):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["sandbox"] = request.headers.get("X-Sandbox-ID")
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"usage": {"estimated_tokens": 7}})

    _mock_transport(monkeypatch, handler)
    with GatewayClient("http://gateway.test", api_key="secret", sandbox_id="demo") as client:
        assert client.sandbox_budget() == {"usage": {"estimated_tokens": 7}}
    assert seen == {"path": "/v1/sandbox/budget", "sandbox": "demo", "auth": "Bearer secret"}


def test_upload_file_and_create_batch(monkeypatch):
    seen = {}

    def handler(request):
        seen[request.url.path] = request.method
        if request.url.path == "/v1/files":
            return httpx.Response(200, json={"id": "file-1", "object": "file", "purpose": "batch"})
        if request.url.path == "/v1/batches":
            return httpx.Response(200, json={"id": "batch-1", "object": "batch", "status": "validating"})
        return httpx.Response(404)

    _mock_transport(monkeypatch, handler)
    client = GatewayClient("http://gateway.test", sandbox_id="team-a")
    assert client.upload_batch_file(b'{"custom_id":"a"}\n')["id"] == "file-1"
    batch = client.create_batch("file-1", metadata={"k": "v"})
    assert batch["id"] == "batch-1" and batch["status"] == "validating"
    assert seen == {"/v1/files": "POST", "/v1/batches": "POST"}


def test_batch_lifecycle_and_file_accessors(monkeypatch):
    def handler(request):
        path = request.url.path
        if path == "/v1/batches/batch-1":
            return httpx.Response(200, json={"id": "batch-1", "status": "completed", "output_file_id": "file-out"})
        if path == "/v1/batches/batch-1/cancel":
            return httpx.Response(200, json={"id": "batch-1", "status": "cancelling"})
        if path == "/v1/batches":
            return httpx.Response(200, json={"object": "list", "data": []})
        if path == "/v1/files/file-out/content":
            return httpx.Response(200, content=b'{"custom_id":"a"}\n')
        if path == "/v1/files/file-out":
            return httpx.Response(200, json={"id": "file-out", "object": "file", "deleted": True})
        return httpx.Response(404)

    _mock_transport(monkeypatch, handler)
    client = GatewayClient("http://gateway.test")
    assert client.get_batch("batch-1")["output_file_id"] == "file-out"
    assert client.cancel_batch("batch-1")["status"] == "cancelling"
    assert client.list_batches(limit=5)["object"] == "list"
    assert client.get_file_content("file-out") == b'{"custom_id":"a"}\n'
    assert client.delete_file("file-out")["deleted"] is True


def test_create_and_manage_responses(monkeypatch):
    def handler(request):
        path = request.url.path
        if path == "/v1/responses" and request.method == "POST":
            return httpx.Response(200, json={"id": "resp_1", "object": "response", "status": "completed"})
        if path == "/v1/responses/resp_1" and request.method == "DELETE":
            return httpx.Response(200, json={"id": "resp_1", "object": "response.deleted", "deleted": True})
        if path == "/v1/responses/resp_1/input_items":
            return httpx.Response(200, json={"object": "list", "data": []})
        if path == "/v1/responses/resp_1":
            return httpx.Response(200, json={"id": "resp_1", "status": "completed"})
        return httpx.Response(404)

    _mock_transport(monkeypatch, handler)
    client = GatewayClient("http://gateway.test")
    assert client.create_response("hi", store=True, previous_response_id="resp_0")["id"] == "resp_1"
    assert client.get_response("resp_1")["status"] == "completed"
    assert client.response_input_items("resp_1")["object"] == "list"
    assert client.delete_response("resp_1")["deleted"] is True


def test_sandbox_header_is_omitted_unless_set(monkeypatch):
    # A bound credential must not be overridden by a client-side default sandbox: the
    # gateway rejects a mismatching X-Sandbox-ID with 403.
    seen = []

    def handler(request):
        seen.append(request.headers.get("X-Sandbox-ID"))
        return httpx.Response(200, json={})

    _mock_transport(monkeypatch, handler)
    GatewayClient("http://gateway.test").usage()
    GatewayClient("http://gateway.test", sandbox_id="team-a").usage()
    assert seen == [None, "team-a"]


def test_error_responses_carry_reason_and_request_id(monkeypatch):
    def handler(request):
        return httpx.Response(
            400,
            json={"detail": {"message": "model is not approved", "reason": "model_not_allowed", "request_id": "req-1"}},
        )

    _mock_transport(monkeypatch, handler)
    with pytest.raises(GatewayError) as excinfo:
        GatewayClient("http://gateway.test").chat([{"role": "user", "content": "hi"}], model="x")
    error = excinfo.value
    assert isinstance(error, httpx.HTTPStatusError)
    assert (error.status_code, error.reason, error.request_id) == (400, "model_not_allowed", "req-1")
    assert "model_not_allowed" in str(error)


def test_stream_error_status_raises_with_the_gateway_reason(monkeypatch):
    def handler(request):
        return httpx.Response(400, json={"detail": {"message": "streaming disabled", "reason": "streaming_disabled"}})

    _mock_transport(monkeypatch, handler)
    with pytest.raises(GatewayError) as excinfo:
        list(GatewayClient("http://gateway.test").chat_stream([{"role": "user", "content": "hi"}]))
    assert excinfo.value.reason == "streaming_disabled"


def test_state_creating_calls_are_not_retried_on_ambiguous_failures(monkeypatch):
    # A 502 or a read timeout may mean the batch was created; retrying could create a second.
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(502, json={"detail": "runtime returned an error"})

    _mock_transport(monkeypatch, handler)
    client = GatewayClient("http://gateway.test")
    sleeps = _record_sleeps(monkeypatch, client)
    with pytest.raises(GatewayError):
        client.create_batch("file-1")
    assert calls == ["/v1/batches"]
    assert sleeps == []


def test_state_creating_calls_retry_when_the_gateway_did_not_act(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("refused", request=request)
        if len(calls) == 2:
            return httpx.Response(503, json={"detail": "at capacity"})
        return httpx.Response(200, json={"id": "batch-1"})

    _mock_transport(monkeypatch, handler)
    client = GatewayClient("http://gateway.test")
    _record_sleeps(monkeypatch, client)
    assert client.create_batch("file-1")["id"] == "batch-1"
    assert len(calls) == 3


def test_inference_calls_retry_transport_errors(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadError("reset", request=request)
        return httpx.Response(200, json={"choices": []})

    _mock_transport(monkeypatch, handler)
    client = GatewayClient("http://gateway.test")
    _record_sleeps(monkeypatch, client)
    assert client.chat([{"role": "user", "content": "hi"}]) == {"choices": []}
    assert len(calls) == 2


def test_zero_retries_makes_exactly_one_attempt(monkeypatch):
    calls = []
    _mock_transport(monkeypatch, lambda request: calls.append(1) or httpx.Response(500, json={}))
    with pytest.raises(GatewayError):
        GatewayClient("http://gateway.test", max_retries=0).usage()
    assert calls == [1]


def test_messages_completions_and_receipts_hit_their_routes(monkeypatch):
    bodies = {}

    def handler(request):
        bodies[request.url.path] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    _mock_transport(monkeypatch, handler)
    client = GatewayClient("http://gateway.test")
    client.messages([{"role": "user", "content": "hi"}], max_tokens=64, system="be brief")
    client.completions("Once upon a time", max_tokens=8)
    client.record_receipt("egress_denied", "denied", target="example.com:443")

    assert bodies["/v1/messages"] == {
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 64,
        "system": "be brief",
    }
    assert bodies["/v1/completions"] == {"prompt": "Once upon a time", "max_tokens": 8}
    assert bodies["/v1/receipts"] == {"action_type": "egress_denied", "decision": "denied", "target": "example.com:443"}


def test_custom_transport_and_default_headers():
    seen = {}

    def handler(request):
        seen["traceparent"] = request.headers.get("traceparent")
        return httpx.Response(200, json={"status": "ready"})

    client = GatewayClient(
        "http://gateway.test",
        transport=httpx.MockTransport(handler),
        default_headers={"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"},
    )
    assert client.ready() is True
    assert seen["traceparent"].startswith("00-aaaa")
