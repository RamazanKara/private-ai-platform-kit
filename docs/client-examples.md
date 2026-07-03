# Client API Examples

The gateway is OpenAI-compatible, so any OpenAI client library works by pointing its
base URL at the gateway and sending the platform headers. These examples use the local
port-forward address (`make local-up` then port-forward the gateway service); replace it
with your Ingress host in a customer cluster.

The `model` id must be on the active profile's allowlist: the local lab allows
`qwen2.5:0.5b`, while the customer profiles default to `qwen3.5:0.8b` (Ollama) and
`Qwen/Qwen3-Coder-Next` (vLLM). Streaming is admitted by default in every shipped
profile (`admission.allowStreaming: true`); an air-gapped/regulated deployment that
requires end-of-stream-only guardrail enforcement sets it `false`. Examples below that
use a customer model are marked accordingly.

All business endpoints accept:

- `Authorization: Bearer <api-key-or-jwt>` (or the `X-API-Key` header) when auth is enabled
- `X-Sandbox-ID: <sandbox>` — the tenant/sandbox the request is attributed to
- `X-Request-ID` and W3C `traceparent` — optional, echoed back for tracing

Responses echo `X-Request-ID`, `X-Sandbox-ID`, and `traceparent` for correlation. When
sandbox budgets are enabled, `/v1/chat/completions` (including streaming) and
`/v1/embeddings` responses also carry the OpenAI-style budget headers that agent
frameworks parse to pace themselves:

- `x-ratelimit-limit-requests` / `x-ratelimit-remaining-requests` — the sandbox request
  budget and what remains of it in the current window
- `x-ratelimit-limit-tokens` / `x-ratelimit-remaining-tokens` — the estimated-token budget
  and what remains of it, floored at zero

Each pair is present only when the corresponding limit is configured (greater than zero);
cache hits (`X-Cache: HIT`) consume no budget and omit them. See the
[budget controls runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/budget-controls.md) for sizing and triage.

## curl

```bash
GATEWAY=http://127.0.0.1:8080
KEY=local-development-only

# Chat completion
curl -fsS "$GATEWAY/v1/chat/completions" \
  -H "Authorization: Bearer $KEY" -H "X-Sandbox-ID: demo" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"hello"}]}'

# Embeddings
curl -fsS "$GATEWAY/v1/embeddings" \
  -H "Authorization: Bearer $KEY" -H "X-Sandbox-ID: demo" \
  -H 'Content-Type: application/json' \
  -d '{"input":"embed this text"}'

# Legacy text completion (prompt-based; governed like chat, non-streaming)
curl -fsS "$GATEWAY/v1/completions" \
  -H "Authorization: Bearer $KEY" -H "X-Sandbox-ID: demo" \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Write a haiku about the sea.","max_tokens":64}'

# Moderations (content policy classification)
curl -fsS "$GATEWAY/v1/moderations" \
  -H "Authorization: Bearer $KEY" -H "X-Sandbox-ID: demo" \
  -H 'Content-Type: application/json' \
  -d '{"input":"text to classify"}'
```

## Moderations taxonomy (not OpenAI's harm categories)

`/v1/moderations` is OpenAI-compatible in shape but classifies against the platform
*governance* taxonomy, not OpenAI's harm taxonomy. The response carries a top-level
`"taxonomy": "governance"` marker and the `categories`/`category_scores` keys are
`credential`, `pii`, and `blocked_terms` (rule-based credential/PII/denylist detection),
not `hate`, `violence`, `self-harm`, etc. Branch on the `taxonomy` field if you consume
both a real OpenAI moderation endpoint and this one. A semantic toxicity classifier can be
layered behind the same endpoint later without changing callers.

## Legacy `/v1/completions` (prompt-based)

The gateway also exposes the pre-chat `/v1/completions` endpoint for tools that still use a
`prompt` (a string or list of strings) instead of `messages`. It runs through the **same**
governance path as chat — model allowlist, admission limits, prompt secret policy, sandbox
budget, output guardrail, and audit — so legacy-completion traffic is not a control bypass.
Streaming is **not** supported on `/v1/completions` in this release (send `stream: false`,
or use `/v1/chat/completions` for streaming); a streaming request is rejected with a clear
`streaming_not_supported` error. Prefer `/v1/chat/completions` for new integrations.

## Error responses

Every gateway error body is OpenAI-shaped:

```json
{
  "error": {
    "message": "requested completion tokens is 50; limit is 10",
    "type": "invalid_request_error",
    "code": "max_tokens_too_large",
    "request_id": "…",
    "sandbox_id": "demo"
  },
  "detail": { "…": "original detail, preserved this release" }
}
```

`error.type` follows the OpenAI taxonomy (`invalid_request_error`,
`authentication_error`, `permission_error`, `rate_limit_error`, `api_error`, …) so an
OpenAI SDK's typed exceptions (e.g. `RateLimitError`) map correctly. `error.code` carries
the gateway's machine reason. The legacy `detail` object is preserved alongside for one
release while callers migrate; do not depend on it long-term. Pydantic request-validation
errors (HTTP 422) keep FastAPI's default `{"detail": [...]}` shape.

## Python (openai SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key="local-development-only",
    default_headers={"X-Sandbox-ID": "demo"},
)

# Tool-calling (the flagship coding-agent path) is forwarded to the runtime.
resp = client.chat.completions.create(
    model="qwen2.5:0.5b",
    messages=[{"role": "user", "content": "What is the weather in Berlin?"}],
    tools=[{
        "type": "function",
        "function": {
            "name": "get_weather",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        },
    }],
)
print(resp.choices[0].message)

# Streaming (admitted by default in every shipped profile)
for chunk in client.chat.completions.create(
    model="qwen3.5:0.8b",
    messages=[{"role": "user", "content": "stream a haiku"}],
    stream=True,
):
    print(chunk.choices[0].delta.content or "", end="")
```

## Python (plain httpx, no SDK)

```python
import httpx

gateway = "http://127.0.0.1:8080"
headers = {"Authorization": "Bearer local-development-only", "X-Sandbox-ID": "demo"}

r = httpx.post(
    f"{gateway}/v1/chat/completions",
    headers=headers,
    json={"messages": [{"role": "user", "content": "hello"}]},
    timeout=120,
)
r.raise_for_status()
print(r.json()["choices"][0]["message"]["content"])
```

## Python (first-party client)

The kit ships a minimal, retry-aware first-party client (`sdk/python`, packaged as
`private-ai-platform-kit-client`) for scripts that do not want the full `openai` dependency:

```python
from ai_platform_client import GatewayClient

with GatewayClient("http://127.0.0.1:8080", api_key="local-development-only", sandbox_id="demo") as gw:
    print(gw.chat([{"role": "user", "content": "hello"}])["choices"][0]["message"]["content"])
    for delta in gw.chat_stream([{"role": "user", "content": "stream a haiku"}]):
        print(delta, end="")
```

## Agent & coding frameworks (drop-in)

Because the gateway is OpenAI-compatible, agent and coding frameworks work by pointing their
OpenAI base URL at the gateway and adding the `X-Sandbox-ID` header. Always route framework traffic
through the gateway (not the runtime directly) so auth, model allowlists, budgets, guardrails, and
audit apply.

### LangChain

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key="local-development-only",
    model="qwen2.5:0.5b",
    default_headers={"X-Sandbox-ID": "demo"},
)
print(llm.invoke("hello").content)
```

### LlamaIndex

```python
from llama_index.llms.openai_like import OpenAILike

llm = OpenAILike(
    api_base="http://127.0.0.1:8080/v1",
    api_key="local-development-only",
    model="qwen2.5:0.5b",
    is_chat_model=True,
    default_headers={"X-Sandbox-ID": "demo"},
)
print(llm.complete("hello"))
```

### Aider (coding agent)

Assumes a customer vLLM profile serving `Qwen/Qwen3-Coder-Next`; point the base URL at
your gateway host.

```bash
export OPENAI_API_BASE=http://127.0.0.1:8080/v1
export OPENAI_API_KEY=local-development-only
# Aider forwards no custom header, so bind the sandbox with a JWT tenant claim (auth.jwt.tenantClaim)
# or run Aider from inside an agent-workspace namespace whose egress sets X-Sandbox-ID at the proxy.
aider --model openai/Qwen/Qwen3-Coder-Next
```

### Continue / Cline (VS Code)

Point the assistant at an OpenAI-compatible provider with `apiBase:
http://<gateway-host>/v1`, the API key, and a `requestOptions.headers` entry setting
`X-Sandbox-ID`. The gateway's `/v1/models` lists the approved models to configure.

> Frameworks that cannot set a custom header should bind the sandbox with a JWT tenant claim
> (`auth.jwt.tenantClaim`) so per-sandbox budgets and attribution cannot be spoofed.

## Anthropic SDK / Claude-style agents (translation sidecar)

The gateway speaks the **OpenAI chat-completions** protocol. It does **not** implement the
native Anthropic Messages API (`/v1/messages`). Anthropic-SDK or Claude-style agents that
require `/v1/messages` can still run against the gateway by putting a small protocol
**translation sidecar** in front of it — for example a [LiteLLM](https://docs.litellm.ai/)
proxy that exposes an Anthropic-shaped `/v1/messages` endpoint and forwards to the gateway's
`/v1/chat/completions`. The sidecar does the Anthropic-to-OpenAI request/response
translation; the gateway still applies auth, model allowlists, budgets, guardrails, and
audit. Point the sidecar's upstream at the gateway and set the platform headers on the
forwarded request.

Minimal LiteLLM config sketch (`config.yaml`), with the gateway as the OpenAI-compatible
upstream:

```yaml
model_list:
  - model_name: claude-shim            # the name Anthropic-SDK clients request
    litellm_params:
      model: openai/qwen2.5:0.5b       # an allowlisted gateway model
      api_base: http://<gateway-host>/v1
      api_key: os.environ/GATEWAY_API_KEY
      extra_headers:
        X-Sandbox-ID: demo             # or bind the sandbox via a JWT tenant claim
```

```bash
litellm --config config.yaml   # serves an Anthropic-compatible /v1/messages
```

Anthropic-SDK clients then point `base_url` at the sidecar (not the gateway directly). This
is a translation shim, not native support: features without an OpenAI chat-completions
equivalent are limited by what the sidecar can map. See
[Scope and non-goals](scope-and-non-goals.md) for the exact list of protocol surfaces the
gateway does and does not implement.
