# Client API Examples

The gateway is OpenAI-compatible, so any OpenAI client library works by pointing its
base URL at the gateway and sending the platform headers. These examples use the local
port-forward address (`make local-up` then port-forward the gateway service); replace it
with your Ingress host in a customer cluster.

All business endpoints accept:

- `Authorization: Bearer <api-key-or-jwt>` (or the `X-API-Key` header) when auth is enabled
- `X-Sandbox-ID: <sandbox>` — the tenant/sandbox the request is attributed to
- `X-Request-ID` and W3C `traceparent` — optional, echoed back for tracing

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
    model="qwen3.5:0.8b",
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

# Streaming
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
