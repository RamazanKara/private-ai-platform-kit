# Inference gateway

This FastAPI service exposes the governed inference APIs. It authenticates callers,
applies sandbox and model policy, reserves and settles budgets, routes requests to
Ollama or vLLM, and emits metrics and redacted audit receipts.

Use the [developer workflow](../../docs/development.md) for environment setup and
the [feature inventory](../../docs/feature-inventory.md) for supported API behavior.

## Code map

| Area | Modules |
| --- | --- |
| Application assembly, middleware, and lifecycle | [main.py](app/main.py) |
| Request identity, authentication, and error envelopes | [request_context.py](app/request_context.py), [jwt_auth.py](app/jwt_auth.py), [key_records.py](app/key_records.py) |
| Shared admission, settlement, and receipt handling | [governance.py](app/governance.py), [admission.py](app/admission.py), [budget.py](app/budget.py) |
| Chat, completions, embeddings, and moderations | [inference_api.py](app/inference_api.py), [schemas.py](app/schemas.py) |
| Messages and Responses protocols | [messages_api.py](app/messages_api.py), [messages.py](app/messages.py), [responses_api.py](app/responses_api.py), [responses.py](app/responses.py) |
| Files and asynchronous Batch jobs | [batch_api.py](app/batch_api.py), [batch_worker.py](app/batch_worker.py), [batchstore.py](app/batchstore.py) |
| Runtime transport, fallback, and streaming | [runtime_client.py](app/runtime_client.py), [runtime_routing.py](app/runtime_routing.py), [streaming.py](app/streaming.py) |
| Configuration and model/sandbox policy | [settings.py](app/settings.py), [env_config.py](app/env_config.py), [policy.py](app/policy.py) |
| Audit and agent-action receipts | [audit.py](app/audit.py), [receipts.py](app/receipts.py), [sandbox_api.py](app/sandbox_api.py) |
| Persistence and caching | [objectstore.py](app/objectstore.py), [objectstore_s3.py](app/objectstore_s3.py), [response_store.py](app/response_store.py), [cache.py](app/cache.py) |

Start a new governed inference handler from the patterns in `inference_api.py` and
`governance.py`. Streaming handlers finish settlement and recording when the stream
ends. Preserve that lifecycle when changing cancellation or failure handling.

## Test and run locally

From the repository root:

```bash
make test-gateway
```

This prepares the hashed development environment, runs the gateway suite, and runs
the first-party SDK suite. Tests use fake backends; shared test helpers are in
[tests/gateway_support.py](tests/gateway_support.py).

For an interactive server using an already running local Ollama instance:

```bash
OLLAMA_BASE_URL=http://127.0.0.1:11434 MODEL_ID=qwen3.5:0.8b \
  src/inference-gateway/.venv/bin/python -m uvicorn app.main:app \
  --app-dir src/inference-gateway --host 127.0.0.1 --port 8080 --reload
```

Set `MODEL_ID` to a model already installed in that Ollama instance. The command
does not download a model. Open `http://127.0.0.1:8080/docs` for the local API.
This loopback development server uses environment defaults; deployed authentication
and policy come from the chart and cluster values.

## Related contracts

- [API contract](../../platform/api-contracts/inference-gateway.openapi.json):
  regenerate with `make api-contract-update` after public route/schema changes.
- [Configuration contract](../../platform/config-contracts/inference-gateway.config.json):
  regenerate with `make config-contract-update` after settings or chart changes.
- [Helm chart](../../deploy/charts/inference-gateway/README.md) and
  [API access runbook](../../runbooks/api-access.md).
- [Audit runbook](../../runbooks/audit-chain.md) for exported chain verification.
