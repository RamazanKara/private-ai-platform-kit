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

## Rejection Behavior

When a prompt matches a configured secret/PII pattern the gateway returns HTTP 400 with
reason `prompt_secret_detected`; a blocked-term match returns `content_blocked`. Either
increments `inference_gateway_admission_rejections_total` and does not forward the request.

The rejection message names the matched pattern but does not echo the matched text. Audit logs continue to record prompt length and hash only.

## Operational Guidance

Keep this enabled for coding-agent and tenant workspaces. Disable it only for controlled tests that intentionally validate secret-handling behavior, and keep those tests in a private sandbox.

If a legitimate prompt is blocked, inspect the matched pattern name, remove the credential material from the prompt, or narrow the configured pattern list for that sandbox after review.
