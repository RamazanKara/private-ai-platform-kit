# Evaluation Harness Runbook

Use this runbook when running repeatable prompt checks against the local lab or a customer-owned gateway.

## What The Harness Checks

The evaluation harness reads an `EvalSuite`, sends each case to `POST /v1/chat/completions`, and records:

- HTTP success or failure
- latency in milliseconds
- response length
- simple expected-text checks
- forbidden-text checks for secret-leak regression cases
- per-case pass or fail status

The default smoke suite is intentionally small. The coding-agent suite adds checks for change planning, secret handling, prompt-injection boundaries, and incident triage. These suites are regression tools, not benchmarks or substitutes for domain-specific human review.

## Validate Suite Syntax

Run this without a live cluster:

    services/inference-gateway/.venv/bin/python scripts/eval-suite.py --suite evals/smoke-suite.yaml --check-config
    services/inference-gateway/.venv/bin/python scripts/eval-suite.py --suite evals/coding-agent-suite.yaml --check-config

Expected output:

    eval suite OK: evals/smoke-suite.yaml (2 case(s))
    eval suite OK: evals/coding-agent-suite.yaml (4 case(s))

`make validate` runs this syntax check automatically.

## Run Against The Local Gateway

Start and sync the local lab first:

    make local-up
    LOCAL_DIRECT_APPLY=1 make sync
    kubectl -n ollama exec ollama-0 -- ollama pull qwen2.5:0.5b

Then run:

    make eval

Run the coding-agent readiness suite:

    SUITE=evals/coding-agent-suite.yaml make eval

The wrapper port-forwards the inference gateway, runs the suite, and writes evidence under `results/evals/`.

If a gateway is already reachable, bypass port-forwarding:

    GATEWAY_URL=http://127.0.0.1:18082 make eval

## Interpret Failures

If a case fails because the response is empty, check runtime health and gateway logs:

    kubectl -n inference logs deploy/inference-gateway-inference-gateway --tail=100
    kubectl -n ollama logs statefulset/ollama --tail=100

If a case fails an expected-text check, inspect the generated Markdown summary and decide whether the prompt, model, or expected check should change. Keep evaluation suite changes reviewed because they define the lab's regression signal.

If a case fails with HTTP 400, inspect the response reason. Common causes are model allowlist rejection, admission limits, or sandbox budget limits.

## Add A Case

Add a new item under `spec.cases`:

    - id: short-stable-name
      description: What this case proves.
      messages:
        - role: user
          content: Prompt text.
      checks:
        minChars: 1
        containsAny:
          - expected phrase
        forbiddenAny:
          - text that must not appear

Prefer short prompts with deterministic expected checks for the local smoke suite. Put larger or domain-specific tests in a separate suite and pass it with `SUITE=evals/<name>.yaml make eval`.
