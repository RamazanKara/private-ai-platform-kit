from typing import Any

import httpx

from app.settings import Settings


REDACTED_MESSAGE_FIELDS = {"reasoning", "reasoning_content", "thinking"}


def sanitize_chat_completion(data: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(data)
    choices = sanitized.get("choices")
    if not isinstance(choices, list):
        return sanitized
    sanitized_choices: list[Any] = []
    for choice in choices:
        if not isinstance(choice, dict):
            sanitized_choices.append(choice)
            continue
        sanitized_choice = dict(choice)
        message = sanitized_choice.get("message")
        if isinstance(message, dict):
            sanitized_choice["message"] = {
                key: value
                for key, value in message.items()
                if key not in REDACTED_MESSAGE_FIELDS
            }
        sanitized_choices.append(sanitized_choice)
    sanitized["choices"] = sanitized_choices
    return sanitized


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
        return sanitize_chat_completion(data)
