# Gateway Guardrails Runbook

Use this runbook when tuning gateway-side controls that block unsafe, expensive, or sensitive prompts before they reach Ollama or vLLM.

## Prompt Secret Detection

The gateway can reject requests that appear to contain credential material. This protects coding-agent workflows where prompts may accidentally include repository files, shell output, environment variables, or copied secrets.

The built-in pattern names are:

- `private_key`
- `github_token`
- `slack_token`
- `bearer_token`
- `generic_api_key_assignment`

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

## Rejection Behavior

When a prompt matches a configured pattern, the gateway returns HTTP 400 with reason `prompt_secret_detected`, increments `inference_gateway_admission_rejections_total`, and does not forward the request to the runtime.

The rejection message names the matched pattern but does not echo the secret text. Audit logs continue to record prompt length and hash only.

## Operational Guidance

Keep this enabled for coding-agent and tenant workspaces. Disable it only for controlled tests that intentionally validate secret-handling behavior, and keep those tests in a private sandbox.

If a legitimate prompt is blocked, inspect the matched pattern name, remove the credential material from the prompt, or narrow the configured pattern list for that sandbox after review.
