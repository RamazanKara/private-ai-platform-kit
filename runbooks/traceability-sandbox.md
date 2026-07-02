# Traceability And Sandbox Runbook

Use this runbook when proving that a local lab request can be traced without leaking prompt text, or when debugging a customer sandbox.

## Request Contract

Every caller should send:

- `X-Request-ID`: a request correlation id. The gateway generates one when it is missing.
- `X-Sandbox-ID`: a lowercase sandbox id such as `local-lab` or `team-a-lab`.
- `X-API-Key`: required for gateway and RAG business endpoints when API-key authentication is enabled.
- `traceparent`: optional W3C trace context propagated to the runtime.

The gateway returns `X-Request-ID` and `X-Sandbox-ID` on every successful response and forwards those headers to Ollama or vLLM.

The gateway also enforces `ALLOWED_MODELS` when it is set. A request with an unapproved `model` returns HTTP 400 before it reaches the runtime.

Admission controls reject requests that exceed the configured message count, prompt size, completion-token ceiling, temperature range, streaming policy, or prompt secret-detection policy. Rejections return HTTP 400 and are counted by `inference_gateway_admission_rejections_total`.

Sandbox budgets track accepted request count, prompt characters, and estimated tokens per `X-Sandbox-ID` inside each gateway process. A budget overage returns HTTP 400 with a reason such as `sandbox_request_budget_exceeded` or `sandbox_token_budget_exceeded`.

## Local Proof

From the repository root, run:

    make local-up
    make bootstrap-argocd
    make sync
    make smoke RUNTIME_BACKEND=ollama
    make trace-smoke

`make smoke` validates the gateway response headers through a port-forward and sends the local demo API key by default. `make trace-smoke` runs a Kubernetes Job in `ai-sandbox`, where quota, default resource limits, restricted pod security labels, and default-deny network policy are present.

## Audit Log Shape

Gateway audit log lines are JSON. They include:

    event
    request_id
    traceparent
    sandbox_id
    backend
    model
    status_code
    runtime_status_code
    latency_ms
    message_count
    message_roles
    prompt_chars
    prompt_sha256
    usage
    error

The audit event intentionally excludes raw prompt and completion text. Use `prompt_sha256` only for correlation between controlled test inputs and audit records.

## Kubernetes Checks

Inspect the sandbox controls:

    kubectl get namespace ai-sandbox --show-labels
    kubectl -n ai-sandbox get resourcequota,limitrange,networkpolicy
    kubectl -n ai-sandbox logs job/ai-sandbox-trace-smoke

Inspect recent gateway audit logs:

    kubectl -n inference logs deploy/inference-gateway-inference-gateway --tail=100 | grep inference_request

If a request is rejected with HTTP 400, verify that `X-Sandbox-ID` uses only lowercase letters, numbers, and hyphens and is no longer than 63 characters.

Inspect sandbox budget usage:

    kubectl -n inference port-forward svc/inference-gateway-inference-gateway 18082:8080
    curl -sS -H 'X-Sandbox-ID: local-lab' http://127.0.0.1:18082/v1/sandbox/budget

If the response detail mentions `invalid_or_missing_api_key`, verify the caller's API key and configured SHA-256 hash. If the response detail mentions `ALLOWED_MODELS`, update the approved model catalog or ask the caller to use an approved model. If the response mentions `prompt_secret_detected`, remove the credential material from the prompt or tune guardrail patterns only after review. If the response mentions prompt size, message count, completion tokens, temperature, streaming, or sandbox budget, adjust the sandbox limits only after confirming capacity and policy requirements.
