from typing import Any

import httpx

from app.settings import Settings


class RuntimeClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def chat_completions(
        self,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        body = dict(payload)
        body["model"] = body.get("model") or self.settings.model_id
        url = f"{self.settings.runtime_base_url}/v1/chat/completions"
        timeout = httpx.Timeout(self.settings.request_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=body, headers=headers)
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise ValueError("runtime returned a non-object JSON response")
        return data
