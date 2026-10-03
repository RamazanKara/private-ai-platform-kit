# private-ai-platform-kit-client

Python client for the [Private AI Platform Kit](https://github.com/RamazanKara/private-ai-platform-kit)
inference gateway: a self-hosted, OpenAI- and Anthropic-compatible LLM gateway that enforces
model allowlists, sandbox budgets, and guardrails, and writes a tamper-evident receipt for every call.

The only dependency is `httpx`. The package ships inline type annotations (`py.typed`).

```bash
python -m pip install https://github.com/RamazanKara/private-ai-platform-kit/releases/download/v0.29.0/private_ai_platform_kit_client-0.29.0-py3-none-any.whl
```

```python
from ai_platform_client import GatewayClient, GatewayError

with GatewayClient("http://127.0.0.1:8080", api_key="local-development-only") as gw:
    reply = gw.chat([{"role": "user", "content": "Summarize the release notes."}])
    print(reply["choices"][0]["message"]["content"])

    for chunk in gw.chat_stream([{"role": "user", "content": "Stream a haiku."}]):
        print(chunk, end="", flush=True)

    gw.messages([{"role": "user", "content": "Hello"}], max_tokens=128)  # Anthropic shape
    print(gw.usage())                                                    # tokens and cost so far

    try:
        gw.chat([{"role": "user", "content": "hi"}], model="not-approved")
    except GatewayError as exc:
        print(exc.status_code, exc.reason, exc.request_id)  # 400 model_not_allowed req-...
```

## What it covers

| Area | Methods |
| --- | --- |
| OpenAI-compatible inference | `chat`, `chat_stream`, `completions`, `embeddings`, `moderations`, `batch` |
| Anthropic Messages | `messages` |
| Files and asynchronous batches | `upload_batch_file`, `create_batch`, `get_batch`, `list_batches`, `cancel_batch`, file accessors |
| Responses API | `create_response`, `get_response`, `delete_response`, `response_input_items` |
| Agent-action receipts | `record_receipt` |
| Accounting and health | `models`, `usage`, `sandbox_budget`, `ready` |

## Behavior worth knowing

- **Sandbox header.** `sandbox_id` is sent as `X-Sandbox-ID` only when you set it. Leave it
  unset for a credential the gateway binds to a sandbox; naming a different sandbox is rejected.
- **Errors.** Every error response raises `GatewayError` (a subclass of
  `httpx.HTTPStatusError`) with `status_code`, the gateway's machine-readable `reason`, and the
  `request_id` to look the call up in the audit trail.
- **Retries.** Transient failures (429 and 5xx, connection errors) are retried with
  exponential backoff, honoring `Retry-After` up to `retry_after_cap` (30 s by default). A
  longer advertised delay, typically an exhausted budget window, raises
  `GatewayRetryAfterError` immediately with a `retry_after` attribute. Calls that create
  server-side state (uploads, batches, stored responses, receipts) are retried only when the
  gateway provably did not act: a connection failure, a 429, or a 503.
- **Streaming** is never retried once bytes flow; a terminal gateway error event raises
  `GatewayStreamError` so a truncated stream is not mistaken for a complete one.
- **Transport.** Pass `transport=` (any `httpx.BaseTransport`), `verify=` (a CA bundle path),
  or `default_headers=` (for example a `traceparent`) to the constructor.

For async I/O, typed response models, or the full OpenAI and Anthropic parameter surface,
point the official `openai` or `anthropic` SDK at the gateway's base URL; the gateway applies
the same governance either way. See the
[client examples](https://ramazankara.github.io/private-ai-platform-kit/latest/client-examples/).

Licensed under Apache-2.0.
