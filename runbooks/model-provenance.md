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

Reports are written under `results/model-provenance/`.

## Digest scopes

The approved Ollama models use `model-artifact` digests: the model-weights layer
checksum returned by the registry manifest. The approved Hugging Face models use
`artifact-manifest` digests: the SHA-256 of a checked-in JSON inventory containing
every safetensors filename, byte size, and upstream LFS SHA-256 at an immutable
commit. See [the inventory format](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/platform/model-catalog/artifacts/README.md).

The legacy `source-reference` scope identifies only a source pointer. It remains
supported for externally maintained records, but no approved bundled model uses it.

Run the optional network check to reproduce approved upstream metadata:

```bash
make model-provenance-verify
```

This fetches small registry/API responses. It does not download model weights or
verify the bytes in a customer model store. During ingestion, compare each downloaded
weight file's size and SHA-256 with the inventory. Keep that artifact verification
and real-model evaluation evidence with the deployment's release evidence.

Keep provenance changes reviewed with the matching `ModelPromotionRequest`. Do not
add a model to gateway allowlists until `make model-check`,
`make model-provenance-check`, and `make release-gate-strict` pass with current evidence.

## Pinning the Served Revision

A bare Hugging Face repo id resolves against a mutable default branch. The default
vLLM chart and approved customer profiles therefore set `model.revision` to the
full commit recorded in provenance. The chart passes it to vLLM as `--revision`.
The embedding profile uses its own model's commit. If a custom overlay changes
`model.name`, it must also change `model.revision`.

To rotate a Hugging Face model revision:

1. Resolve and review a full upstream commit SHA, including configuration and license changes.
2. Generate its weight inventory into a temporary review path:

   ```bash
   python3 scripts/model_artifacts.py \
     --model Qwen/Qwen3-Coder-Next \
     --revision a7fbcb5c0e12d62a448eaa0e260346bf5dcc0feb \
     --output .out/model-artifacts/qwen3-coder-next.json
   ```

3. Review the inventory diff, then update the checked-in inventory, provenance
   `revision`, `artifactManifest`, digest, and `immutableRef` together. The immutable
   reference format is `huggingface://owner/model@COMMIT#sha256:MANIFEST_DIGEST`.
4. Update every corresponding serving profile and model card in the same change.
5. Run `make model-provenance-check`, `make model-provenance-verify`, and the
   real-model evaluation and capacity tests before rollout.

The offline gate checks manifest integrity, identity, and serving-revision parity.
The network check additionally rebuilds the inventory from the pinned upstream
metadata. A failed check blocks promotion; do not change a digest merely to silence
the check without reviewing the upstream change.
