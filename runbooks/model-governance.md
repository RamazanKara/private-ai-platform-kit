# Model Governance Runbook

Use this runbook when adding, approving, deprecating, blocking, or reviewing models in the Private AI Platform Kit catalog.

## Required Artifacts

Every approved model must have:

- an entry in `model-catalog/models.yaml`
- lifecycle status, owner, runtime, stage, risk tier, data classification, license, and source metadata
- context window, prompt limit, and completion limit metadata
- supported accelerator metadata
- model artifact provenance in `governance/model-provenance.yaml`
- a matching `ModelPromotionRequest` under `model-catalog/promotion-requests/`
- evaluation, load-test, and security workflow evidence references
- gateway allowlist entries only after approval

The cluster-facing catalog ConfigMap at `model-catalog/k8s/configmap.yaml` must embed the same catalog content as `model-catalog/models.yaml`.

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

1. Add the model to `model-catalog/models.yaml` with `status: proposed`.
2. Add a `ModelPromotionRequest` under `model-catalog/promotion-requests/`.
3. Add artifact provenance under `governance/model-provenance.yaml`, including source URI, immutable reference, digest, license, risk, data classification, and serving profiles.
4. Run an evaluation suite and keep the Markdown summary under `results/evals/`.
5. Run a load test appropriate for the target runtime and keep the summary under `results/loadtest/`.
6. Confirm the image, runtime, or serving stack is covered by CI security controls.
7. Change the catalog status to `approved` only after review.
8. Add the model to the gateway `runtime.allowedModels` values for the approved environment.
9. Run `make model-check`, `make model-provenance-check`, and `make validate`.

For a deprecated or blocked model, remove it from all gateway allowlists before changing the status.
