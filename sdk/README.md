# Client SDKs

First-party, dependency-light clients for the Private AI Platform Kit inference gateway.

## Python

Install the released client from PyPI:

```bash
python -m pip install private-ai-platform-kit-client
```

[`python/ai_platform_client.py`](python/ai_platform_client.py) is a single-module `GatewayClient`
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
`GatewayStreamError` on a terminal gateway error event). Retries honor the gateway's
`Retry-After` header: the client waits the longer of the exponential backoff and the advertised
delay, as long as the header stays within the `retry_after_cap` constructor argument
(default `30.0` seconds). If the gateway advertises a delay beyond the cap (typically a
budget-window 429, where `Retry-After` is the time until the window resets and can be hours),
the client raises `GatewayRetryAfterError` immediately without sleeping, because retrying
sooner cannot succeed; its `retry_after` attribute says how many seconds to wait before coming
back. `GatewayRetryAfterError` subclasses `httpx.HTTPStatusError`, so existing handlers keep
working. For typed response models and the full
OpenAI parameter surface, point the official
[`openai` SDK](https://github.com/openai/openai-python) at the gateway base URL instead. See
[docs/client-examples.md](../docs/client-examples.md).

Release tags are built in an unprivileged CI job and published through PyPI Trusted
Publishing with Sigstore attestations. The package version must exactly match the Git tag;
the release workflow rejects mismatches before publishing.
