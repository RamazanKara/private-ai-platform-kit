# Model Card: qwen2.5:0.5b

This card restates the governed facts for `qwen2.5:0.5b`. The sources of truth are
`platform/model-catalog/models.yaml`, `platform/governance/model-provenance.yaml`, and
`platform/model-catalog/promotion-requests/qwen2.5-local-lab-approved.yaml`. If this card and
those files disagree, the YAML is authoritative.

## Identity

- Model id: `qwen2.5:0.5b`
- Runtime: ollama
- Modality: text-generation
- Owner: platform-team
- Lifecycle status: approved
- Stage: local-lab

## Intended use

Default local CPU smoke model. It is the model the laptop `kind` quickstart and demo flow target
so the end-to-end path (gateway -> Ollama -> chat completion) completes in seconds on a CPU-only
machine. It is the only model in the `local` gateway allowlist
(`deploy/clusters/local/values/inference-gateway.yaml`). Use it to validate that the platform is
wired correctly, not to judge model quality.

## Out-of-scope / not approved for

- Customer or production environments. It is allowlisted only in `local`; the customer Ollama
  profile uses `qwen3.5:0.8b` instead.
- Reasoning-heavy or coding-agent workloads. It is a fast, non-reasoning 0.5B model chosen for
  demo latency, not answer quality.
- Any use beyond the local lab requires its own promotion request and review.

## Runtime and serving profile

- Serving runtime: Ollama
- Accelerators: cpu
- Context window: 32768 tokens
- Gateway admission: maxPromptChars 8192, maxCompletionTokens 1024
- Serving profile values: `deploy/clusters/local/values/ollama.yaml`,
  `deploy/clusters/local/values/inference-gateway.yaml`
- Gateway allowlist: local

## Provenance

- Source: ollama-library (`ollama://qwen2.5:0.5b`)
- Immutable reference:
  `ollama-library/qwen2.5:0.5b@sha256:c5396e06af294bd101b30dce59131a76d2b773e76950acc870eda801d3ab0515`
- Digest: `sha256:c5396e06af294bd101b30dce59131a76d2b773e76950acc870eda801d3ab0515`
  (scope: model-artifact)
- Verification: `ollama-registry-model-layer`. Resolve the model-weights layer digest from the
  Ollama registry manifest:

      curl -fsSL https://registry.ollama.ai/v2/library/qwen2.5/manifests/0.5b | \
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

- 0.5B parameters: low answer quality, no reliable reasoning or tool-use behaviour. Selected for
  speed on CPU, not capability.
- CPU-only serving; throughput and latency are bounded by the host CPU.
- Intended as a smoke/demo model, so its admission limits (8192 prompt chars, 1024 completion
  tokens) are deliberately small.

## Evaluation evidence

- Eval suite: `platform/evals/smoke-suite.yaml`
- Eval summary: `results/evals/sample-summary.md`
- Load-test summary: `results/loadtest/sample-summary.md`
- Security workflow: `.github/workflows/ci.yml`

## Approval reference

- Promotion request: `platform/model-catalog/promotion-requests/qwen2.5-local-lab-approved.yaml`
- Requested by: platform-team
- Approvers: model-governance-board
- Business justification: Fast non-reasoning Qwen2.5 0.5B promoted as the default local CPU smoke
  model so the laptop `kind` quickstart completes in seconds; the larger `qwen3.5:0.8b` reasoning
  model moved to the customer Ollama profile.
