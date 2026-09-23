# Pinned weight inventories

Each JSON file records the safetensors files published at one immutable Hugging Face
commit. `schemaVersion`, `modelId`, and `revision` identify the inventory. Each
`files` entry records a relative path, byte size, and upstream LFS SHA-256 checksum.

The SHA-256 of the JSON file is stored in
[model-provenance.yaml](../../governance/model-provenance.yaml) with
`digest.scope: artifact-manifest`. It identifies this inventory; it is not the
checksum of concatenated model weights.

Generate an inventory for a reviewed revision from the repository root:

```bash
python3 scripts/model_artifacts.py \
  --model Qwen/Qwen3-Coder-Next \
  --revision a7fbcb5c0e12d62a448eaa0e260346bf5dcc0feb \
  --output .out/model-artifacts/qwen3-coder-next.json
```

Review the resulting file before replacing a checked-in inventory and updating
its provenance and serving revisions. `make model-provenance-check` validates
local consistency; `make model-provenance-verify` reproduces the inventory from
the upstream metadata API. Neither downloads the weights. A customer model store
must separately verify the downloaded files against the recorded checksums.
