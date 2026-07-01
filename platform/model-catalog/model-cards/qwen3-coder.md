# Model Card: Qwen/Qwen3-Coder-Next

This card restates the governed facts for `Qwen/Qwen3-Coder-Next`. The sources of truth are
`platform/model-catalog/models.yaml`, `platform/governance/model-provenance.yaml`, and
`platform/model-catalog/promotion-requests/qwen3-coder-customer-lab-approved.yaml`. If this card
and those files disagree, the YAML is authoritative.

## Identity

- Model id: `Qwen/Qwen3-Coder-Next`
- Runtime: vllm
- Modality: text-generation
- Owner: platform-team
- Lifecycle status: approved
- Stage: customer-lab

## Intended use

OpenAI-compatible coding-agent model for customer GPU clusters. It is the recommended coding
profile for customer-owned labs and is allowlisted in the `customer` gateway
(`deploy/clusters/customer/values/inference-gateway.yaml`). It is served by vLLM via the customer
vLLM profiles and is intended for coding-agent workspace validation, not the local laptop flow.

## Out-of-scope / not approved for

- Local or CPU-only environments. It requires multi-GPU serving and is not in the `local`
  allowlist. It cannot be run in CI.
- Production sign-off on the bundled evidence alone. The cited eval suite was run against a
  CPU-runnable proxy (`qwen3.5:0.8b`), so the suite must be re-run against the real model on the
  customer GPU profile before production sign-off (see Evaluation evidence).
- Air-gapped operation as configured: its promotion request declares
  `externalNetworkRequired: true` (weights are pulled from Hugging Face). Pre-stage weights into a
  private model store for offline serving.

## Runtime and serving profile

- Serving runtime: vLLM (OpenAI-compatible)
- Accelerators: nvidia, amd
- Context window: 262144 tokens
- Gateway admission: maxPromptChars 131072, maxCompletionTokens 8192
- Serving profile values: `deploy/clusters/customer/values/vllm.yaml`,
  `deploy/clusters/customer/values/vllm-nvidia.yaml`,
  `deploy/clusters/customer/values/vllm-amd.yaml`,
  `deploy/clusters/customer/values/inference-gateway.yaml`
- Gateway allowlist: customer

The default NVIDIA profile (`vllm.yaml`) requests 4 GPUs per replica and scales on request-queue
depth via KEDA; it persists weights on a shared `ReadWriteMany` volume. Pin the served revision to
the attested provenance commit via `model.revision` before production
(see `runbooks/model-provenance.md`).

## Provenance

- Source: huggingface (`https://huggingface.co/Qwen/Qwen3-Coder-Next`)
- Revision: `main` (pin to a specific Hugging Face commit before production)
- Immutable reference:
  `huggingface://Qwen/Qwen3-Coder-Next@sha256:71b3fd269cc80811780298417132e23cc6272e7fea40c36733c6caffa6e1ee06`
- Digest: `sha256:71b3fd269cc80811780298417132e23cc6272e7fea40c36733c6caffa6e1ee06`
  (scope: source-reference)
- Verification: `customer-model-store` — `huggingface-cli scan-cache --dir /models`
- License: apache-2.0

The bundled digest is a source-reference pointer, not a model-artifact checksum. Replace it with
the customer's pinned model-store artifact revision and checksum before production use.

## Data classification and risk

- Data classification: internal
- Risk tier: medium
- Prompt logging: redacted
- External network required: true
- Requires GPU: true

## Known limitations

- Multi-GPU only: cannot be served on CPU and cannot be exercised in CI.
- Eval evidence is via a proxy model; real-model evaluation on the customer GPU profile is a
  prerequisite for production sign-off.
- Requires external network to fetch weights unless they are pre-staged into a private model
  store.
- Served weights can drift from the attested revision if `model.revision` is left unpinned.

## Evaluation evidence

- Eval suite: `platform/evals/coding-agent-suite.yaml`
- Eval model proxy: `qwen3.5:0.8b` — `Qwen3-Coder-Next` requires multi-GPU serving and cannot be
  run in CI; `qwen3.5:0.8b` is the CPU-runnable proxy exercised by the coding-agent eval suite.
  Re-run the suite against the real model on the customer GPU profile before production sign-off.
- Eval summary: `results/evals/sample-summary.md`
- Load-test summary: `results/loadtest/sample-summary.md`
- Security workflow: `.github/workflows/ci.yml`

## Approval reference

- Promotion request:
  `platform/model-catalog/promotion-requests/qwen3-coder-customer-lab-approved.yaml`
- Requested by: platform-team
- Approvers: model-governance-board
- Business justification: Current OpenAI-compatible Qwen3 Coder Next profile for customer GPU
  coding-agent validation.
