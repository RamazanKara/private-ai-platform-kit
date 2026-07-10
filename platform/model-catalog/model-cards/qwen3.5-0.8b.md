# Model Card: qwen3.5:0.8b

This card restates the governed facts for `qwen3.5:0.8b`. The sources of truth are
`platform/model-catalog/models.yaml`, `platform/governance/model-provenance.yaml`, and
`platform/model-catalog/promotion-requests/qwen3.5-customer-lab-approved.yaml`. If this card and
those files disagree, the YAML is authoritative.

## Identity

- Model id: `qwen3.5:0.8b`
- Runtime: ollama
- Modality: text-generation
- Owner: platform-team
- Lifecycle status: approved
- Stage: customer-lab

## Intended use

Higher-quality small reasoning model for the customer Ollama profile. It is allowlisted in the
`customer` gateway (`deploy/clusters/customer/values/inference-gateway.yaml`) as the CPU-served
chat model for customer labs that want better answer quality than the local smoke default. It also
serves as the CPU-runnable eval proxy for the multi-GPU `Qwen/Qwen3-Coder-Next` coding-agent suite
(see that model's card).

## Out-of-scope / not approved for

- The local laptop profile. It reasons slowly on a CPU-only laptop, so the local quickstart uses
  `qwen2.5:0.5b` instead; `qwen3.5:0.8b` is not in the `local` allowlist.
- High-throughput or low-latency production serving on CPU. It is a reasoning model and completions
  can be slow without acceleration.

## Runtime and serving profile

- Serving runtime: Ollama
- Accelerators: cpu
- Context window: 32768 tokens
- Gateway admission: maxPromptChars 8192, maxCompletionTokens 1024
- Serving profile values: `deploy/clusters/customer/values/ollama.yaml`,
  `deploy/clusters/customer/values/inference-gateway.yaml`
- Gateway allowlist: customer

## Provenance

- Source: ollama-library (`ollama://qwen3.5:0.8b`)
- Immutable reference:
  `ollama-library/qwen3.5:0.8b@sha256:afb707b6b8fac6e475acc42bc8380fc0b8d2e0e4190be5a969fbf62fcc897db5`
- Digest: `sha256:afb707b6b8fac6e475acc42bc8380fc0b8d2e0e4190be5a969fbf62fcc897db5`
  (scope: model-artifact)
- Verification: `ollama-registry-model-layer`. Resolve the model-weights layer digest from the
  Ollama registry manifest:

      curl -fsSL https://registry.ollama.ai/v2/library/qwen3.5/manifests/0.8b | \
        jq -r '.layers[] | select(.mediaType=="application/vnd.ollama.image.model").digest'

- License: apache-2.0

The digest is the Ollama registry model-weights layer and is reproducible via the command above.
Re-verify it against your own model store before any production use.

## Data classification and risk

- Data classification: internal
- Risk tier: low
- Prompt logging: redacted
- External network required: false
- Requires GPU: false

## Known limitations

- 0.8B parameters: a small reasoning model. Better quality than `qwen2.5:0.5b` but well below the
  larger customer GPU models.
- Reasons slowly on CPU-only hosts; this is why the local laptop profile does not use it.
- CPU-only serving; throughput and latency are bounded by the host CPU.

## Evaluation evidence

- Eval suite: `platform/evals/coding-agent-suite.yaml`
- Eval summary: `results/evals/sample-summary.md`
- Load-test summary: `results/loadtest/sample-summary.md`
- Security workflow: `.github/workflows/ci.yml`

## Approval reference

- Promotion request:
  `platform/model-catalog/promotion-requests/qwen3.5-customer-lab-approved.yaml`
- Requested by: platform-team
- Approvers: model-governance-board
- Business justification: Higher-quality small Qwen3.5 reasoning model for the customer Ollama
  profile. It reasons slowly on CPU, so the local laptop profile uses the faster `qwen2.5:0.5b`
  instead.
