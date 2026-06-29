# Model Provenance Runbook

Use this runbook when approving or serving a model in the local lab or a customer-owned cluster.

## Policy

Model artifact provenance lives in `platform/governance/model-provenance.yaml`.

Every approved model must have:

- source URI
- immutable reference with a SHA-256 digest
- digest scope and verification command
- license, risk tier, and data classification matching the model catalog
- matching promotion request
- serving profiles that reference the model
- evaluation, load-test, and security evidence references

## Validate Provenance

Run:

    make model-provenance-check

Generate JSON and Markdown evidence:

    make model-provenance-report

Reports are written under `.out/results/model-provenance/`.

## Customer Production Use

The bundled local lab uses source-reference digests so the governance workflow is runnable without a private model store. For customer production, replace those with model-artifact digests from the customer's registry, object store, Hugging Face mirror, or model artifact repository.

Keep model provenance changes reviewed with the matching `ModelPromotionRequest`. Do not add a model to gateway allowlists until `make model-check`, `make model-provenance-check`, and `make release-gate-strict` pass with current evidence.
