"""Input and output guardrail helpers."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.metrics import OUTPUT_GUARDRAIL, PROMPT_GUARDRAIL
from app.settings import Settings


def _guardrail_targets(choice: dict[str, Any], legacy_completion: bool) -> list[tuple[str, dict[str, Any], str]]:
    """Return writable generated-text fields subject to the output policy.

    In addition to visible assistant content, model-generated tool and legacy
    function arguments are executable output. Scanning those fields closes the
    path where a credential or denied value could bypass the response guardrail
    and be handed directly to a tool runner.
    """
    if legacy_completion:
        text = choice.get("text")
        if not isinstance(text, str) or not text:
            return []
        return [(text, choice, "text")]
    message = choice.get("message")
    if not isinstance(message, dict):
        return []
    targets: list[tuple[str, dict[str, Any], str]] = []
    content = message.get("content")
    if isinstance(content, str) and content:
        targets.append((content, message, "content"))
    function_call = message.get("function_call")
    if isinstance(function_call, dict):
        arguments = function_call.get("arguments")
        if isinstance(arguments, str) and arguments:
            targets.append((arguments, function_call, "arguments"))
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            arguments = function.get("arguments")
            if isinstance(arguments, str) and arguments:
                targets.append((arguments, function, "arguments"))
    return targets


def _apply_output_guardrail(
    response: dict[str, Any] | None,
    settings: Settings,
    route: str,
    request: Request,
    *,
    legacy_completion: bool = False,
) -> None:
    """Inspect the runtime completion and flag/redact/block per the output guardrail.

    Runs the configured credential/PII/blocked-term detectors on each choice's generated
    text (OWASP LLM02:2025 sensitive information disclosure / LLM05:2025 improper output handling).
    ``flag`` records only; ``redact`` rewrites matched spans in place; ``block`` withholds
    the content and sets ``finish_reason=content_filter``. Mutates ``response`` in place so
    the redacted/blocked body is what gets cached, audited, and returned. ``legacy_completion``
    scans/rewrites the completion ``choice.text`` field instead of chat ``message.content``.
    """
    if not settings.output_guardrail_enabled or not isinstance(response, dict):
        return
    choices = response.get("choices")
    if not isinstance(choices, list):
        return
    action: str | None = None
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        for text, container, key in _guardrail_targets(choice, legacy_completion):
            if settings.output_guardrail_mode == "redact":
                redacted, matched = settings.redact_output_text(text)
                if matched:
                    container[key] = redacted
                    action = "redacted"
            else:
                patterns, terms = settings.output_findings(text)
                if patterns or terms:
                    if settings.output_guardrail_mode == "block":
                        container[key] = "[response withheld by output policy]"
                        choice["finish_reason"] = "content_filter"
                        action = "blocked"
                    else:
                        action = action or "flagged"
    if action:
        OUTPUT_GUARDRAIL.labels(action, route).inc()
        request.state.output_guardrail_action = action


def _apply_prompt_secret_mode(settings: Settings, payload: dict[str, Any], route: str) -> str | None:
    """Redact or flag prompt secrets (redact/flag modes); return the action taken or None.

    Block mode is enforced earlier in admission; this handles the non-rejecting modes so
    an agent reading a ``.env`` or a lockfile does not kill its own conversation while the
    credential is still kept out of the runtime call (redact) or recorded (flag). Returning
    the action (rather than writing request state directly) lets the batch path attribute it
    to the individual item instead of clobbering one shared per-request field.
    """
    matched = settings.apply_prompt_secret_mode(payload)
    if not matched:
        return None
    action = "redacted" if settings.prompt_secret_mode == "redact" else "flagged"
    PROMPT_GUARDRAIL.labels(action, route).inc()
    return action
