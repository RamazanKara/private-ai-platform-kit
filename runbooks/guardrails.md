# Gateway Guardrails Runbook

Use this runbook when tuning gateway-side controls that block unsafe, expensive, or sensitive prompts before they reach Ollama or vLLM.

## Prompt Secret Detection

The gateway can reject requests that appear to contain credential material. This protects coding-agent workflows where prompts may accidentally include repository files, shell output, environment variables, or copied secrets.

The built-in credential pattern names (enabled by default) are:

- `private_key`
- `github_token`
- `slack_token`
- `bearer_token`
- `generic_api_key_assignment`

Three PII detectors are also built in but **opt-in** (emails appear in many legitimate
prompts, so they are not enabled by default):

- `email`
- `us_ssn`
- `credit_card`

Add them to `patterns` to reject PII alongside credentials.

Configure the guardrail in Helm values:

    guardrails:
      promptSecretDetection:
        enabled: true
        patterns:
          - private_key
          - github_token
          - slack_token
          - bearer_token
          - generic_api_key_assignment

The chart passes this as `PROMPT_SECRET_DETECTION_ENABLED` and `PROMPT_SECRET_PATTERNS`.

## Blocked-Term Denylist

`guardrails.blockedContentTerms` is a list of case-insensitive substrings rejected
anywhere in chat or embedding input (for example internal project codenames). A match
returns HTTP 400 with reason `content_blocked`. Empty disables the denylist. The chart
passes this as `BLOCKED_CONTENT_TERMS`.

## Moderations Endpoint

`POST /v1/moderations` returns an OpenAI-compatible moderation result that classifies
each input against the built-in `credential`, `pii`, and `blocked_terms` categories
without forwarding it to a runtime. It is a deterministic content-policy surface; a
semantic toxicity/jailbreak classifier can be layered behind the same endpoint without
changing callers.

## Output Guardrail (Response Path)

Input moderation cannot catch a credential or PII value that the *model* emits: a successful
prompt injection or a hallucinated secret leaves the gateway in the completion. The output
guardrail inspects the model response before it is returned or cached, closing OWASP LLM02
(insecure output handling) and LLM06 (sensitive information disclosure).

Configure it in Helm values:

    guardrails:
      outputGuardrail:
        enabled: true
        mode: redact        # flag | redact | block
        patterns:
          - private_key
          - github_token
          - slack_token
          - bearer_token
          - generic_api_key_assignment
          - email
          - us_ssn
          - credit_card

The chart passes this as `OUTPUT_GUARDRAIL_ENABLED`, `OUTPUT_GUARDRAIL_MODE`, and
`OUTPUT_GUARDRAIL_PATTERNS`. The same `blockedContentTerms` denylist applies to output too.

Modes:

- `flag`: record the finding only (metric + `X-Output-Guardrail: flagged` header); content
  is returned unchanged. Use to measure exposure before enforcing.
- `redact`: replace each matched span with `[REDACTED:<pattern>]` (default). The redacted
  body is what gets returned, cached, and audited, so a leaked secret is never persisted.
- `block`: withhold the content (`[response withheld by output policy]`) and set the choice
  `finish_reason` to `content_filter`.

Each action increments `inference_gateway_output_guardrail_total{action,route}` and sets the
`X-Output-Guardrail` response header.

Streaming responses are **detected and flagged** only (`flagged_stream`): the bytes are already
on the wire, so the guardrail cannot redact or block them mid-stream. For hard redact/block
enforcement, run callers non-streaming (the default `admission.allowStreaming: false`).

Treat model output as untrusted: a coding agent must never pass a completion to a shell, `eval`,
or file write without its own validation, regardless of the gateway guardrail.

## Rejection Behavior

When a prompt matches a configured secret/PII pattern the gateway returns HTTP 400 with
reason `prompt_secret_detected`; a blocked-term match returns `content_blocked`. Either
increments `inference_gateway_admission_rejections_total` and does not forward the request.

The rejection message names the matched pattern but does not echo the matched text. Audit logs continue to record prompt length and hash only.

## Operational Guidance

Keep this enabled for coding-agent and tenant workspaces. Disable it only for controlled tests that intentionally validate secret-handling behavior, and keep those tests in a private sandbox.

If a legitimate prompt is blocked, inspect the matched pattern name, remove the credential material from the prompt, or narrow the configured pattern list for that sandbox after review.
