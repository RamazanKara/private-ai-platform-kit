# Client API Examples

The gateway is OpenAI-compatible, so any OpenAI client library works by pointing its
base URL at the gateway and sending the platform headers. These examples use the local
port-forward address (`make local-up` then port-forward the gateway service); replace it
with your Ingress host in a customer cluster.

The `model` id must be on the active profile's allowlist: the local lab allows
`qwen2.5:0.5b` (and disables streaming via `admission.allowStreaming: false`), while the
customer profiles default to `qwen3.5:0.8b` (Ollama) and `Qwen/Qwen3-Coder-Next` (vLLM).
Examples below that stream or use a customer model are marked accordingly.

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

# Moderations (content policy classification)
curl -fsS "$GATEWAY/v1/moderations" \
  -H "Authorization: Bearer $KEY" -H "X-Sandbox-ID: demo" \
  -H 'Content-Type: application/json' \
  -d '{"input":"text to classify"}'
```

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

# Streaming (customer profiles only: the local lab disables streaming admission)
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
