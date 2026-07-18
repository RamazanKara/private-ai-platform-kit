from app.schemas import BatchRequest, ChatCompletionRequest


def test_chat_completion_schema_preserves_provider_fields() -> None:
    payload = ChatCompletionRequest.model_validate(
        {
            "model": "qwen2.5-coder",
            "messages": [{"role": "user", "content": "hello", "cache_control": {"type": "ephemeral"}}],
            "top_p": 0.8,
            "seed": 7,
        }
    )

    assert payload.model_dump(exclude_none=True) == {
        "model": "qwen2.5-coder",
        "messages": [
            {
                "role": "user",
                "content": "hello",
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "stream": False,
        "top_p": 0.8,
        "seed": 7,
    }


def test_batch_schema_builds_nested_chat_requests() -> None:
    payload = BatchRequest.model_validate(
        {
            "requests": [
                {
                    "messages": [{"role": "developer", "content": "Be concise."}],
                    "temperature": 0.2,
                }
            ]
        }
    )

    assert isinstance(payload.requests[0], ChatCompletionRequest)
    assert payload.requests[0].messages[0].role == "developer"
