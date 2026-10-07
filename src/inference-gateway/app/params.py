"""Which caller-supplied request fields the gateway forwards to a runtime.

The gateway used to forward every unknown field verbatim. That made it a faithful proxy,
and also let a caller reach runtime extensions that step around gateway controls: vLLM's
``best_of`` and beam search multiply compute past the ``n`` cap, ``chat_template`` replaces
the server's prompt template, ``logits_processors`` loads code by name, and ``priority``
jumps a shared queue. Request fields now fall into three tiers per endpoint:

- forwarded: the OpenAI parameters for the endpoint and reviewed runtime extensions
  (guided decoding and the common extra samplers);
- refused with ``400 parameter_not_allowed``: extensions that defeat a gateway control;
- dropped and reported in the ``X-Dropped-Params`` response header: everything else, so an
  unreviewed runtime feature never reaches the model silently and a client can see why its
  field had no effect.

Operators can forward additional fields with ``EXTRA_FORWARDED_PARAMS``; a name listed
there is forwarded even when it would otherwise be refused, as a deliberate choice.
"""

from __future__ import annotations

from typing import Any

from app.admission import AdmissionPolicyError

_OPENAI_SAMPLING = frozenset(
    {
        "model",
        "frequency_penalty",
        "presence_penalty",
        "logit_bias",
        "logprobs",
        "top_logprobs",
        "max_tokens",
        "max_completion_tokens",
        "n",
        "seed",
        "stop",
        "stream",
        "stream_options",
        "temperature",
        "top_p",
        "user",
        "metadata",
        "service_tier",
        "store",
        "safety_identifier",
        "prompt_cache_key",
    }
)

# Runtime extensions reviewed as harmless to the gateway's controls: they shape sampling or
# constrain output format, and none of them raises cost beyond the admitted token caps.
_REVIEWED_EXTENSIONS = frozenset(
    {
        "top_k",
        "min_p",
        "repetition_penalty",
        "length_penalty",
        "stop_token_ids",
        "include_stop_str_in_output",
        "skip_special_tokens",
        "spaces_between_special_tokens",
        "guided_json",
        "guided_regex",
        "guided_choice",
        "guided_grammar",
        "guided_whitespace_pattern",
        "guided_decoding_backend",
        "structured_outputs",
        "cache_salt",
    }
)

FORWARDED_PARAMS: dict[str, frozenset[str]] = {
    "chat": _OPENAI_SAMPLING
    | _REVIEWED_EXTENSIONS
    | frozenset(
        {
            "messages",
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "functions",
            "function_call",
            "response_format",
            "reasoning_effort",
            "verbosity",
            "modalities",
            "audio",
            "prediction",
            "web_search_options",
        }
    ),
    "completions": _OPENAI_SAMPLING | _REVIEWED_EXTENSIONS | frozenset({"prompt", "suffix", "echo", "best_of"}),
    "embeddings": frozenset({"model", "input", "dimensions", "encoding_format", "user", "truncate_prompt_tokens"}),
}

# Refused outright, with the control each one would defeat.
REFUSED_PARAMS: dict[str, str] = {
    "use_beam_search": "multiplies runtime work past the n limit",
    "prompt_logprobs": "returns per-token prompt data and multiplies response size",
    "chat_template": "replaces the server's prompt template",
    "chat_template_kwargs": "changes how the server renders the prompt template",
    "add_generation_prompt": "changes how the server renders the prompt template",
    "continue_final_message": "changes how the server renders the prompt template",
    "logits_processors": "loads processing code by name inside the runtime",
    "ignore_eos": "forces every completion to the maximum length",
    "min_tokens": "forces a minimum completion length",
    "priority": "reorders the runtime's shared request queue",
    "vllm_xargs": "passes arbitrary arguments to runtime plugins",
    "kv_transfer_params": "addresses the runtime's key-value cache transfer layer",
    "mm_processor_kwargs": "changes multimodal preprocessing inside the runtime",
}


def apply_param_policy(payload: dict[str, Any], endpoint: str, extra_forwarded: tuple[str, ...] = ()) -> list[str]:
    """Filter ``payload`` in place for ``endpoint``; return the names that were dropped.

    Raises :class:`AdmissionPolicyError` (``parameter_not_allowed``) for a refused field,
    including ``best_of`` greater than 1, which generates that many completions per
    returned one and so sidesteps the ``n`` cap.
    """
    forwarded = FORWARDED_PARAMS[endpoint]
    extra = frozenset(extra_forwarded)
    for name in sorted(payload):
        if name in extra:
            continue
        if name in REFUSED_PARAMS:
            raise AdmissionPolicyError(
                "parameter_not_allowed",
                f"parameter '{name}' is not allowed through this gateway: it {REFUSED_PARAMS[name]}",
            )
        if name == "best_of" and _int_above_one(payload[name]):
            raise AdmissionPolicyError(
                "parameter_not_allowed",
                "best_of greater than 1 is not allowed through this gateway: "
                "it multiplies runtime work past the n limit",
            )
    dropped = [
        name for name in payload if name not in forwarded and name not in extra and name != "data_classification"
    ]
    for name in dropped:
        del payload[name]
    return sorted(dropped)


def _int_above_one(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 1
