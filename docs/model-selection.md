# Model selection and updates

Model metadata was reviewed against the publishers' repositories on **2026-09-23**
for the v0.29.0 release. The catalog separates models approved for the existing lab
profiles from newer candidates that still need evaluation.

## Models used by the shipped profiles

| Purpose | Model | Release behavior |
| --- | --- | --- |
| Local CPU smoke tests | `qwen2.5:0.5b` | Retains the small, non-reasoning model used by the quickstart. Its Ollama weight-layer digest was reverified. |
| Customer CPU lab | `qwen3.5:0.8b` | Retains the customer reasoning model. Its Ollama weight-layer digest was reverified. |
| Customer coding agents | `Qwen/Qwen3-Coder-Next` | Now pins the upstream commit and all safetensors shard checksums in a reproducible inventory. |
| Customer RAG embeddings | `BAAI/bge-small-en-v1.5` | Now pins the upstream commit and weight inventory; the embedding deployment uses the same revision. |

These approvals describe lab profiles. They do not establish production suitability
for a customer's workloads. Read the [model cards](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/platform/model-catalog/model-cards/README.md)
for the existing evidence and limitations.

## Current GPU candidates

All rows below have `status: proposed` and are absent from the gateway allowlists.
Context sizes are upstream configuration values, not measured capacity or enabled
gateway limits. Candidate licenses and revision links are recorded in
[`platform/model-catalog/models.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/platform/model-catalog/models.yaml).

| Candidate | Upstream license | Context tokens | Evaluation focus |
| --- | --- | --- | --- |
| [Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B) | Apache-2.0 | 262,144 | Dense model for coding and agent workloads; first candidate to compare with the current coding profile. |
| [Qwen3.8-Flash-Next](https://huggingface.co/Qwen/Qwen3.8-Flash-Next) | Qwen Community 1.0 | 262,144 | Larger architecture with custom license terms and model-specific serving requirements. |
| [GLM-5.3-Flash](https://huggingface.co/zai-org/GLM-5.3-Flash) | MIT | 1,048,576 | Large multi-GPU candidate; validate the publisher's serving recipe and hardware requirements. |
| [DeepSeek-V4.1-Flash](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) | MIT | 1,048,576 | Large model with a specialized architecture; validate runtime support before capacity testing. |
| [Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) | Apache-2.0 | 262,144 | Retained as a comparison candidate with verified upstream context metadata. |

Qwen3.8-Flash-Next is **not** Apache-2.0. Its publisher uses the
[Qwen Community 1.0 license](https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/de4b8e4d43b917e7706784d8bb445c9af86a3540/LICENSE).
The catalog records `LicenseRef-Qwen-Community-1.0` so its terms cannot be confused
with the permissive license of Qwen3.8-27B.

The newer models include upstream multimodal capabilities. Their presence in the
catalog does not add image or video support to the gateway. The pinned runtime
image and existing GPU profiles have not been validated with these candidates.
Use the upstream recipe, then run the kit's real-model evals and load tests before
changing a serving profile or approving a model.

## Reproduce the approved model metadata

```bash
make model-check
make model-provenance-check
make model-provenance-verify
```

The first two commands validate local catalog, manifest, and profile consistency.
The third contacts the Ollama registry and Hugging Face metadata API. It reproduces
the approved Ollama layer digests and Hugging Face weight inventories without
downloading model weights.

A manifest digest identifies the inventory of weight filenames, byte sizes, and
upstream SHA-256 checksums at one immutable commit. It is not a checksum of the
entire model, and it does not verify bytes already installed in a customer model
store. Compare those files against the inventory during model-store ingestion.

See the [provenance runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/model-provenance.md)
for the manifest format and update commands.

## Upgrade from v0.28.1

- The default vLLM chart and approved customer profiles now set `model.revision`.
  Custom overlays that change `model.name` must also set the matching revision.
- The embedding profile explicitly overrides the coding-model pin with the BGE
  revision. Keep these revisions distinct.
- The AWQ example is an operator template. It now names an explicit customer
  checkpoint placeholder instead of implying that an official Qwen AWQ repository
  exists. Supply an approved checkpoint and revision before using it.
- No new candidate is automatically deployed or promoted. Follow the
  [model governance runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/model-governance.md)
  to collect evaluation, load, and security evidence for a promotion.
