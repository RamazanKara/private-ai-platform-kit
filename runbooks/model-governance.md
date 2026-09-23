# Model Governance Runbook

Use this runbook when adding, approving, deprecating, blocking, or reviewing models in the Private AI Platform Kit catalog.

## Required Artifacts

Every approved model must have:

- an entry in `platform/model-catalog/models.yaml`
- lifecycle status, owner, runtime, stage, risk tier, data classification, license, and source metadata
- context window, prompt limit, and completion limit metadata
- supported accelerator metadata
- model artifact provenance in `platform/governance/model-provenance.yaml`
- a matching `ModelPromotionRequest` under `platform/model-catalog/promotion-requests/`
- evaluation, load-test, and security workflow evidence references
- gateway allowlist entries only after approval

The cluster-facing catalog ConfigMap at `platform/model-catalog/k8s/configmap.yaml` must embed the same catalog content as `platform/model-catalog/models.yaml`.

## Validate Governance

Run:

    make model-check
    make model-provenance-check

This verifies catalog schema, approved-only gateway allowlists, promotion requests, evidence paths, vLLM profile model alignment, and ConfigMap parity.

Generate a customer-facing report:

    make model-report
    make model-provenance-report

Reports are written under `results/model-catalog/` and `results/model-provenance/`.

## Promotion Workflow

For a new model:

1. Add the model to `platform/model-catalog/models.yaml` with `status: proposed`.
2. Add a `ModelPromotionRequest` under `platform/model-catalog/promotion-requests/`.
3. Add artifact provenance under `platform/governance/model-provenance.yaml`, including source URI, immutable reference, digest, license, risk, data classification, and serving profiles.
4. Run an evaluation suite and keep the Markdown summary under `results/evals/`.
5. Run a load test appropriate for the target runtime and keep the summary under `results/loadtest/`.
6. Confirm the image, runtime, or serving stack is covered by CI security controls.
7. Change the catalog status to `approved` only after review.
8. Add the model to the gateway `runtime.allowedModels` values for the approved environment.
9. Run `make model-check`, `make model-provenance-check`, and `make validate`.

For a deprecated or blocked model, remove it from all gateway allowlists before changing the status.

## Proposed newer models

The 2026-09-23 review records Qwen3.8-27B, Qwen3.8-Flash-Next, GLM-5.3-Flash,
DeepSeek-V4.1-Flash, and the existing Qwen3.6-35B-A3B comparison candidate. Each
entry links to an immutable upstream model card and records its review date.
See [model selection](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/docs/model-selection.md) for licenses, context sizes,
compatibility limits, and upgrade notes.

These are discovery records, not promotion approvals. They need real-model evals,
load tests, pinned artifact inventories, and reviewed promotion requests before
entering gateway allowlists. In particular, Qwen3.8-Flash-Next uses Qwen Community
1.0 terms; do not copy Apache-2.0 metadata from another Qwen model.

The local CPU smoke and customer CPU profiles retain their existing models and
reverified Ollama weight-layer digests. The approved coding and embedding profiles
now pin the Hugging Face commits recorded in their weight inventories. The
provenance gate rejects drift between those inventories and serving revisions.
