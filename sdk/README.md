# Client SDKs

First-party, dependency-light clients for the Private AI Platform Kit inference gateway.

## Python

[`python/ai_platform_client.py`](python/ai_platform_client.py) — a single-module `GatewayClient`
wrapping the OpenAI-compatible endpoints with the platform headers (`X-Sandbox-ID`, bearer auth).
Its only dependency is `httpx`.

```python
from ai_platform_client import GatewayClient

with GatewayClient("http://127.0.0.1:8080", api_key="local-development-only", sandbox_id="demo") as gw:
    print(gw.chat([{"role": "user", "content": "hello"}]))
    print(gw.embeddings("embed this text"))
    print(gw.moderations("classify this"))
    print(gw.usage())
```

The client includes bounded retry/backoff and a streaming helper (`chat_stream`, which raises
`GatewayStreamError` on a terminal gateway error event). For typed response models and the full
OpenAI parameter surface, point the official
[`openai` SDK](https://github.com/openai/openai-python) at the gateway base URL instead — see
[docs/client-examples.md](../docs/client-examples.md).
