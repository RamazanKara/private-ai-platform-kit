import hashlib
import json
import logging
import struct
import zlib

import httpx
import pytest
import yaml
from app.audit import AUDIT_GENESIS, advance_chain
from app.budget import RedisSandboxBudgetTracker
from app.main import create_app
from app.policy import CLOUD_BACKENDS, ModelRoute, ModelRoutingPolicy, SandboxPolicy, SandboxPolicySet
from fastapi.testclient import TestClient

from tests.gateway_support import FakeRedisBudgetStore, _tool_settings

PROVIDERS = sorted(CLOUD_BACKENDS)
FAKE_CREDENTIAL = "fixture-server-credential-never-log"
CHAT = {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 20}
TOOL = {"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}


def cloud_route(provider, **kwargs):
    return ModelRoute(
        f"cloud-{provider}",
        provider,
        base_url=f"https://fake.invalid/{provider}",
        upstream_model="vendor/model:1",
        credential_env="FIXTURE_PROVIDER_KEY",
        input_usd_per_1k_tokens=1.0,
        output_usd_per_1k_tokens=3.0,
        **kwargs,
    )


def reply(provider, tool=False):
    if provider == "anthropic":
        content = [{"type": "text", "text": "hello"}]
        if tool:
            content.append({"type": "tool_use", "id": "call-1", "name": "lookup", "input": {"q": "hi"}})
        return {
            "id": "msg-1",
            "content": content,
            "stop_reason": "tool_use" if tool else "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 2},
        }
    if provider == "bedrock":
        content = [{"text": "hello"}]
        if tool:
            content.append({"toolUse": {"toolUseId": "call-1", "name": "lookup", "input": {"q": "hi"}}})
        return {
            "output": {"message": {"role": "assistant", "content": content}},
            "stopReason": "tool_use" if tool else "end_turn",
            "usage": {"inputTokens": 5, "outputTokens": 2},
        }
    message = {"role": "assistant", "content": "hello"}
    if tool:
        message["tool_calls"] = [
            {"id": "call-1", "type": "function", "function": {"name": "lookup", "arguments": '{"q":"hi"}'}}
        ]
    return {
        "id": "chat-1",
        "model": "vendor/model:1",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool else "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }


def event_frame(kind, payload):
    headers = b""
    for name, value in ((":message-type", "event"), (":event-type", kind), (":content-type", "application/json")):
        key, val = name.encode(), value.encode()
        headers += bytes([len(key)]) + key + b"\x07" + struct.pack(">H", len(val)) + val
    body = json.dumps(payload).encode()
    prelude = struct.pack(">II", 16 + len(headers) + len(body), len(headers))
    message = prelude + struct.pack(">I", zlib.crc32(prelude)) + headers + body
    return message + struct.pack(">I", zlib.crc32(message))


def streamed_reply(provider):
    if provider == "bedrock":
        return b"".join(
            event_frame(kind, data)
            for kind, data in [
                ("messageStart", {"role": "assistant"}),
                ("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "hello"}}),
                (
                    "contentBlockStart",
                    {"contentBlockIndex": 1, "start": {"toolUse": {"toolUseId": "call-1", "name": "lookup"}}},
                ),
                ("contentBlockDelta", {"contentBlockIndex": 1, "delta": {"toolUse": {"input": '{"q":"hi"}'}}}),
                ("messageStop", {"stopReason": "tool_use"}),
                ("metadata", {"usage": {"inputTokens": 5, "outputTokens": 2}}),
            ]
        )
    if provider == "anthropic":
        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 5, "output_tokens": 0}}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hello"}},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "id": "call-1", "name": "lookup", "input": {}},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"q":"hi"}'},
            },
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 2}},
            {"type": "message_stop"},
        ]
        return b"".join(f"data: {json.dumps(event)}\n\n".encode() for event in events)
    events = [
        {"choices": [{"index": 0, "delta": {"content": "hello"}, "finish_reason": None}]},
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": "lookup", "arguments": '{"q":"hi"}'},
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}},
    ]
    return b"".join(f"data: {json.dumps(event)}\n\n".encode() for event in events) + b"data: [DONE]\n\n"


class FragmentedStream(httpx.AsyncByteStream):
    def __init__(self, content):
        self.content = content

    async def __aiter__(self):
        for start in range(0, len(self.content), 7):
            yield self.content[start : start + 7]


def cloud_app(monkeypatch, routes, handler=None, **settings):
    monkeypatch.setenv("FIXTURE_PROVIDER_KEY", FAKE_CREDENTIAL)
    app = create_app(
        _tool_settings(
            runtime_max_retries=0,
            allow_streaming=True,
            sandbox_budget_enabled=True,
            **settings,
        )
    )
    policy = ModelRoutingPolicy(tuple(routes))
    app.state.model_routing_policy = policy
    runtime = app.state.runtime_client
    runtime.policy = policy
    sent = []

    def transport(request):
        sent.append(request)
        if handler:
            return handler(request)
        provider = request.url.path.split("/")[1]
        body = json.loads(request.content)
        if body.get("stream") or request.url.path.endswith("converse-stream"):
            return httpx.Response(200, stream=FragmentedStream(streamed_reply(provider)))
        return httpx.Response(200, json=reply(provider, tool=True))

    runtime._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    return app, sent


def receipts(caplog):
    return [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "ai_platform_ops_lab.audit" and record.message.startswith("{")
    ]


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("stream", [False, True])
def test_provider_wire_format_governance_usage_and_receipt(monkeypatch, caplog, provider, stream):
    caplog.set_level(logging.INFO)
    route = cloud_route(provider)
    app, sent = cloud_app(monkeypatch, [route])
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                **CHAT,
                "model": route.model_id,
                "tools": [TOOL],
                "stream": stream,
                "stream_options": {"include_usage": True},
            },
            headers={"Authorization": "Bearer caller-key", "baggage": "credential=caller-secret"},
        )
        assert response.status_code == 200, response.text
        if stream:
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
            assert any(event.get("usage", {}).get("total_tokens") == 7 for event in events)
            assert "lookup" in response.text
            assert "[DONE]" in response.text
        else:
            result = response.json()
            assert result["model"] == route.model_id
            assert result["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "lookup"
        usage = client.get("/v1/usage").json()
    assert usage["usage"]["estimated_tokens"] == 7
    assert usage["estimated_cost"] == pytest.approx(0.011)
    assert usage["providers"][provider]["total_tokens"] == 7
    assert len(sent) == 1
    request = sent[0]
    assert "caller" not in str(request.headers)
    assert "baggage" not in request.headers
    if provider == "anthropic":
        assert request.headers["x-api-key"] == FAKE_CREDENTIAL
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert request.url.path.endswith("/messages")
    else:
        assert request.headers["Authorization"] == f"Bearer {FAKE_CREDENTIAL}"
    body = json.loads(request.content)
    if provider == "bedrock":
        assert "vendor%2Fmodel%3A1" in str(request.url)
        assert body["inferenceConfig"]["maxTokens"] == 20
    else:
        assert body["model"] == "vendor/model:1"
    events = receipts(caplog)
    receipt = [event for event in events if event.get("event") == "inference_request"][-1]
    assert receipt["provider"] == provider
    assert receipt["estimated_cost_usd"] == pytest.approx(0.011)
    assert receipt["budget_settlement"]["actual_tokens"] == 7
    assert FAKE_CREDENTIAL not in caplog.text
    assert "caller-secret" not in caplog.text
    previous = AUDIT_GENESIS
    for event in events:
        covered = {key: value for key, value in event.items() if key not in {"prev_hash", "record_hash"}}
        assert advance_chain(previous, covered)[1] == event["record_hash"]
        previous = event["record_hash"]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_cloud_blocks_credentials_and_tenant_budget(monkeypatch, caplog, provider):
    caplog.set_level(logging.INFO)
    route = cloud_route(provider)
    app, sent = cloud_app(monkeypatch, [route], sandbox_request_budget=1)
    with TestClient(app) as client:
        blocked = client.post(
            "/v1/chat/completions",
            json={"model": route.model_id, "messages": [{"role": "user", "content": "ghp_" + "Z" * 36}]},
        )
        assert blocked.status_code == 400
        assert not sent
        assert client.post("/v1/chat/completions", json={**CHAT, "model": route.model_id}).status_code == 200
        assert client.post("/v1/chat/completions", json={**CHAT, "model": route.model_id}).status_code == 429
    assert len(sent) == 1
    assert [event["decision"] for event in receipts(caplog) if event.get("event") == "inference_request"] == [
        "denied",
        "allowed",
        "denied",
    ]


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/v1/chat/completions", CHAT),
        ("/v1/messages", CHAT),
        ("/v1/responses", {"input": "hello"}),
        ("/v1/completions", {"prompt": "hello"}),
        ("/v1/embeddings", {"input": "hello"}),
    ],
)
@pytest.mark.parametrize("tenant", [False, True])
def test_confidential_is_local_only_on_every_endpoint(monkeypatch, caplog, path, payload, tenant):
    caplog.set_level(logging.INFO)
    route = cloud_route("openai")
    app, sent = cloud_app(monkeypatch, [route])
    if tenant:
        app.state.sandbox_policy_set = SandboxPolicySet(
            {"private": SandboxPolicy("private", data_classification="confidential")}
        )
    with TestClient(app) as client:
        response = client.post(
            path,
            json={**payload, "model": route.model_id, "data_classification": "public" if tenant else "confidential"},
            headers={"X-Sandbox-ID": "private"},
        )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "data_classification_denied"
    assert sent == []
    denied = [event for event in receipts(caplog) if event.get("event") == "inference_request"][-1]
    assert denied["provider"] == "openai"
    assert denied["data_classification"] == "confidential"
    assert denied["decision"] == "denied"


@pytest.mark.parametrize("stream", [False, True])
def test_fallback_obeys_classification_and_records_selected_provider(monkeypatch, caplog, stream):
    caplog.set_level(logging.INFO)
    local = ModelRoute("local", "vllm", fallbacks=("cloud-openai",))
    cloud = cloud_route("openai")

    def handler(request):
        if request.url.host == "vllm":
            return httpx.Response(503)
        if stream:
            return httpx.Response(200, content=streamed_reply("openai"))
        return httpx.Response(200, json=reply("openai"))

    app, sent = cloud_app(monkeypatch, [local, cloud], handler)
    with TestClient(app) as client:
        public = client.post("/v1/chat/completions", json={**CHAT, "model": "local", "stream": stream})
        assert public.status_code == 200
        receipt = [event for event in receipts(caplog) if event.get("event") == "inference_request"][-1]
        assert receipt["provider"] == "openai"
        assert [attempt["status"] for attempt in receipt["routing_attempts"]] == ["failed", "served"]
        before = len(sent)
        denied = client.post(
            "/v1/chat/completions",
            json={**CHAT, "model": "local", "stream": stream},
            headers={"X-Data-Classification": "confidential"},
        )
        assert denied.status_code == 403
        assert len(sent) == before + 1
        assert sent[-1].url.host == "vllm"


@pytest.mark.parametrize(
    "path,payload", [("/v1/chat/completions", CHAT), ("/v1/messages", CHAT), ("/v1/responses", {"input": "hello"})]
)
def test_cloud_first_can_fall_back_to_local(monkeypatch, path, payload):
    cloud = cloud_route("openai", fallbacks=("local",))
    local = ModelRoute("local", "vllm")
    app, sent = cloud_app(
        monkeypatch,
        [cloud, local],
        lambda request: httpx.Response(429 if request.url.host == "fake.invalid" else 200, json=reply("openai")),
    )
    with TestClient(app) as client:
        assert client.post(path, json={**payload, "model": cloud.model_id}).status_code == 200
        assert [request.url.host for request in sent] == ["fake.invalid", "vllm"]
        sent.clear()
        assert (
            client.post(
                path, json={**payload, "model": cloud.model_id, "data_classification": "confidential"}
            ).status_code
            == 200
        )
        assert [request.url.host for request in sent] == ["vllm"]


def test_confidential_never_canaries_shadows_or_reads_cloud_cache(monkeypatch):
    cloud = cloud_route("openai")
    local = ModelRoute(
        "local",
        "vllm",
        canary_model_id=cloud.model_id,
        canary_weight=1,
        shadow_model_id=cloud.model_id,
        fallbacks=(cloud.model_id,),
    )
    app, sent = cloud_app(
        monkeypatch,
        [local, cloud],
        lambda request: httpx.Response(200, json=reply("openai")),
        response_cache_enabled=True,
    )
    with TestClient(app) as client:
        assert client.post("/v1/chat/completions", json={**CHAT, "model": "local"}).status_code == 200
        sent.clear()
        response = client.post(
            "/v1/chat/completions", json={**CHAT, "model": "local", "data_classification": "confidential"}
        )
        assert response.status_code == 200
        assert response.headers.get("X-Cache") != "HIT"
        assert sent[-1].url.host == "vllm"


def test_provider_error_and_request_overrides_never_expose_server_credential(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    route = cloud_route("openai", fallbacks=("local",))
    app, sent = cloud_app(
        monkeypatch,
        [route, ModelRoute("local", "vllm")],
        lambda request: httpx.Response(401, json={"error": FAKE_CREDENTIAL}),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                **CHAT,
                "model": route.model_id,
                "api_key": "attacker",
                "base_url": "https://attacker.invalid",
                "credential_env": "OTHER_KEY",
            },
        )
    assert response.status_code == 502
    assert len(sent) == 1
    assert sent[0].headers["Authorization"] == f"Bearer {FAKE_CREDENTIAL}"
    assert "attacker" not in sent[0].content.decode()
    assert FAKE_CREDENTIAL not in response.text + caplog.text


def test_missing_credentials_fail_closed(monkeypatch, caplog):
    route = cloud_route("openai")
    app, sent = cloud_app(monkeypatch, [route])
    monkeypatch.delenv("FIXTURE_PROVIDER_KEY")
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions", json={**CHAT, "model": route.model_id, "api_key": "caller-value"}
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_not_configured"
    assert sent == []


def test_provider_ledger_uses_existing_redis_budget_store(monkeypatch):
    app, _sent = cloud_app(monkeypatch, [cloud_route("openai"), cloud_route("anthropic")])
    redis = FakeRedisBudgetStore()
    app.state.budget_tracker = RedisSandboxBudgetTracker(app.state.settings, client=redis)
    with TestClient(app) as client:
        for provider in ("openai", "anthropic"):
            assert client.post("/v1/chat/completions", json={**CHAT, "model": f"cloud-{provider}"}).status_code == 200
        usage = client.get("/v1/usage").json()
        assert usage["usage"]["estimated_tokens"] == 14
        assert usage["estimated_cost"] == pytest.approx(0.022)
        assert set(usage["providers"]) == {"openai", "anthropic"}
        assert client.get("/v1/usage", headers={"X-Sandbox-ID": "other"}).json()["providers"] == {}


def test_catalog_loading_and_cloud_validation(tmp_path):
    model = {
        "id": "cloud",
        "runtime": "openai",
        "status": "approved",
        "connection": {"baseUrl": "https://fake.invalid/v1", "model": "vendor-model", "credentialEnv": "FIXTURE_KEY"},
        "pricing": {"inputUsdPer1kTokens": 1, "outputUsdPer1kTokens": 2},
    }
    path = tmp_path / "catalog.yaml"
    document = {
        "apiVersion": "platform.ai/v1alpha1",
        "kind": "ModelCatalog",
        "spec": {"models": [model, {"id": "blocked", "runtime": "openai", "status": "blocked"}]},
    }
    path.write_text(yaml.safe_dump(document))
    policy = ModelRoutingPolicy.from_path(path, _tool_settings())
    assert policy.model_ids() == ("cloud",)
    assert policy.routes[0].input_usd_per_1k_tokens == 1
    for connection in (
        {**model["connection"], "apiKey": "inline-secret"},
        {**model["connection"], "baseUrl": "https://user:secret@fake.invalid/v1"},
        {**model["connection"], "baseUrl": "https://fake.invalid/v1?key=secret"},
    ):
        bad = {**model, "connection": connection}
        document["spec"]["models"] = [bad]
        path.write_text(yaml.safe_dump(document))
        with pytest.raises(ValueError):
            ModelRoutingPolicy.from_path(path, _tool_settings())
    for price in (-1, float("nan"), float("inf"), True):
        bad = {**model, "pricing": {"inputUsdPer1kTokens": price, "outputUsdPer1kTokens": 1}}
        document["spec"]["models"] = [bad]
        path.write_text(yaml.safe_dump(document))
        with pytest.raises(ValueError):
            ModelRoutingPolicy.from_path(path, _tool_settings())


@pytest.mark.parametrize("provider", ["anthropic", "bedrock"])
def test_native_tool_result_and_system_translation(monkeypatch, provider):
    app, sent = cloud_app(monkeypatch, [cloud_route(provider)])
    messages = [
        {"role": "system", "content": "be helpful"},
        *CHAT["messages"],
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "lookup", "arguments": '{"q":"hi"}'}}
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "found"},
    ]
    with TestClient(app) as client:
        result = client.post(
            "/v1/chat/completions",
            json={
                **CHAT,
                "messages": messages,
                "model": f"cloud-{provider}",
                "tools": [TOOL],
                "tool_choice": "required",
            },
        )
    assert result.status_code == 200, result.text
    body = json.loads(sent[0].content)
    assert body["system"][0]["text"] == "be helpful"
    if provider == "anthropic":
        assert body["messages"][-1]["content"][0]["tool_use_id"] == "call-1"
        assert body["tool_choice"] == {"type": "any"}
    else:
        assert body["messages"][-1]["content"][0]["toolResult"]["toolUseId"] == "call-1"
        assert body["toolConfig"]["toolChoice"] == {"any": {}}


@pytest.mark.parametrize("provider", PROVIDERS)
def test_truncated_stream_is_receipted_as_failure_without_fallback(monkeypatch, caplog, provider):
    caplog.set_level(logging.INFO)
    route = cloud_route(provider, fallbacks=("local",))
    content = streamed_reply(provider)
    content = content[:-5] if provider == "bedrock" else content[: content.rfind(b"data:")]
    app, sent = cloud_app(
        monkeypatch,
        [route, ModelRoute("local", "vllm")],
        lambda request: httpx.Response(200, stream=FragmentedStream(content)),
    )
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json={**CHAT, "model": route.model_id, "stream": True})
    assert response.status_code == 200
    assert "error" in response.text
    assert len(sent) == 1
    receipt = [event for event in receipts(caplog) if event.get("event") == "inference_request"][-1]
    assert receipt["status_code"] == 502
    assert receipt["provider"] == provider


def test_confidential_batch_items_and_stored_response_cannot_be_downgraded(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    cloud = cloud_route("openai")
    app, sent = cloud_app(
        monkeypatch,
        [cloud, ModelRoute("local", "vllm")],
        lambda request: httpx.Response(200, json=reply("openai")),
        responses_store_enabled=True,
    )
    with TestClient(app) as client:
        batch = client.post(
            "/v1/batch-inference",
            json={
                "data_classification": "confidential",
                "requests": [{**CHAT, "model": cloud.model_id, "data_classification": "public"}],
            },
        )
        assert batch.json()["results"][0]["status_code"] == 403
        assert not sent
        stored = client.post(
            "/v1/responses",
            json={"model": "local", "input": "private", "store": True, "data_classification": "confidential"},
        )
        assert stored.status_code == 200
        sent.clear()
        chained = client.post(
            "/v1/responses",
            json={
                "model": cloud.model_id,
                "input": "continue",
                "previous_response_id": stored.json()["id"],
                "data_classification": "public",
            },
        )
        assert chained.status_code == 403
        assert not sent


@pytest.mark.parametrize("endpoint", ["/v1/chat/completions", "/v1/responses"])
def test_async_batch_preserves_file_classification_and_binds_worker_replay(monkeypatch, tmp_path, caplog, endpoint):
    caplog.set_level(logging.INFO)
    keys = tmp_path / "keys.json"
    keys.write_text(
        json.dumps(
            {
                "records": [
                    {"sha256": hashlib.sha256(b"tenant").hexdigest(), "name": "tenant", "sandbox": "team-a"},
                    {"sha256": hashlib.sha256(b"worker").hexdigest(), "name": "worker", "scopes": ["batch_replay"]},
                ]
            }
        )
    )
    app, sent = cloud_app(
        monkeypatch,
        [cloud_route("openai"), ModelRoute("local", "vllm")],
        batch_api_enabled=True,
        responses_store_enabled=True,
        batch_object_store_backend="memory",
        batch_store_backend="memory",
        api_key_auth_enabled=True,
        api_key_records_path=keys,
    )
    with TestClient(app) as client:
        replay_payload = {**CHAT, "model": "cloud-openai", "data_classification": "public"}
        if endpoint == "/v1/responses":
            prior = client.post(
                endpoint,
                headers={"X-API-Key": "tenant"},
                json={"model": "local", "input": "earlier", "store": True},
            )
            assert prior.status_code == 200
            replay_payload = {
                "model": "cloud-openai",
                "input": "continue",
                "previous_response_id": prior.json()["id"],
                "data_classification": "public",
            }
            sent.clear()
        uploaded = client.post(
            "/v1/files",
            headers={"X-API-Key": "tenant", "X-Data-Classification": "confidential"},
            files={"file": ("in.jsonl", b'{"custom_id":"a","body":{}}\n', "application/jsonl")},
            data={"purpose": "batch"},
        )
        assert uploaded.status_code == 200
        created = client.post(
            "/v1/batches",
            headers={"X-API-Key": "tenant"},
            json={
                "input_file_id": uploaded.json()["id"],
                "endpoint": "/v1/chat/completions",
                "data_classification": "public",
            },
        )
        assert created.status_code == 200
        record = app.state.batch_store.get_batch("team-a", created.json()["id"])
        assert record.data_classification == "confidential"
        app.state.batch_store.update_batch("team-a", record.id, {"status": "in_progress"})
        refused = client.post(
            endpoint,
            headers={
                "X-API-Key": "worker",
                "X-Sandbox-ID": "team-a",
                "X-Batch-ID": record.id,
                "X-Data-Classification": "public",
            },
            json=replay_payload,
        )
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "data_classification_denied"
    assert not sent
    assert receipts(caplog)[-1]["data_classification"] == "confidential"


@pytest.mark.parametrize("provider", ["anthropic", "bedrock", "vertex"])
def test_unsupported_cloud_endpoint_is_explicit_and_receipted(monkeypatch, caplog, provider):
    caplog.set_level(logging.INFO)
    app, sent = cloud_app(monkeypatch, [cloud_route(provider)])
    with TestClient(app) as client:
        result = client.post("/v1/embeddings", json={"model": f"cloud-{provider}", "input": "hello"})
    assert result.status_code == 400
    assert result.json()["error"]["code"] == "provider_endpoint_not_supported"
    assert not sent
    assert receipts(caplog)[-1]["provider"] == provider
