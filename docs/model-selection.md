# Model selection and updates

## Cloud routes (unreleased)

The gateway supports `openai`, `anthropic`, `azure-openai`, `bedrock`, and `vertex`
alongside `ollama` and `vllm`. Cloud templates in the existing model catalog are
**proposed**, with placeholder model IDs, limits, and prices. They enable no external
traffic. Review provider terms, region, retention, model limits, and input/output
prices, then use the existing promotion workflow. `MODEL_ROUTING_POLICY_PATH` accepts
a `ModelCatalog` (only `status: approved` entries) or the existing `ModelRoutingPolicy`.
The catalog checker carries `connection`, `pricing`, and routing fields into the
approved environment's `routing.policy.models`.

An operator's Helm/GitOps values can contain:

```yaml
runtime:
  modelId: qwen2.5:0.5b
  allowedModels: [qwen2.5:0.5b, approved-cloud]
providerCredentials:
  - env: OPENAI_API_KEY
    secretName: model-provider-credentials
    secretKey: openai-api-key
routing:
  policy:
    enabled: true
    models:
      - id: qwen2.5:0.5b
        backend: ollama
        fallbacks: [approved-cloud]
      - id: approved-cloud
        backend: openai
        connection:
          baseUrl: https://api.openai.com/v1
          model: YOUR_APPROVED_MODEL_ID
          credentialEnv: OPENAI_API_KEY
        pricing:
          inputUsdPer1kTokens: 0 # replace with your contracted rate
          outputUsdPer1kTokens: 0 # replace with your contracted rate
sandboxPolicy:
  policy:
    enabled: true
    policies:
      - sandboxId: private-team
        dataClassification: confidential
```

Create the referenced Secret using your existing secret backend; do not put credential
values in Git, catalog entries, Helm values, or client requests. Each connection reads
only its named environment variable. Credential-bearing URLs and inline credential
fields are rejected. Use HTTPS for real providers. The gateway's default NetworkPolicy
still denies external egress: provision an approved HTTPS egress policy for the
gateway namespace through the existing egress catalog, or route through an internal
egress proxy permitted by `networkPolicy.runtimeEgress`. Workspaces retain their
default-deny boundary and receive no provider credentials.

| Backend | `connection.baseUrl` | `connection.model` / credential environment |
| --- | --- | --- |
| `openai` | `https://api.openai.com/v1` | Approved model ID / `OPENAI_API_KEY` |
| `anthropic` | `https://api.anthropic.com/v1` | Approved Claude model ID / `ANTHROPIC_API_KEY` |
| `azure-openai` | `https://RESOURCE.openai.azure.com/openai/v1` | Azure deployment name / `AZURE_OPENAI_API_KEY` |
| `bedrock` | `https://bedrock-runtime.REGION.amazonaws.com` | Bedrock model ID or inference profile / `AWS_BEARER_TOKEN_BEDROCK` |
| `vertex` | `https://REGION-aiplatform.googleapis.com/v1/projects/PROJECT/locations/REGION/endpoints/openapi` | `google/APPROVED_GEMINI_MODEL` / `VERTEX_ACCESS_TOKEN` |

These adapters use [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create),
[Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create),
[Azure's v1 API](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/switching-endpoints),
[Bedrock Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)
with a [Bedrock bearer API key](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys-use.html),
and [Vertex's OpenAI-compatible API](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/samples/generativeaionvertexai-gemini-chat-completions-non-streaming).
Vertex needs an OAuth access token; the operator must refresh it and roll the gateway
when an environment-backed Secret changes. Automatic credential acquisition/refresh
and AWS SigV4 are not implemented. Readiness checks cloud credential presence, not
provider availability or account permissions; actual calls use the shared circuit
breaker and retry policy.

All five support chat, function tools, and streaming through Chat and Messages;
Responses and synchronous batch items retain non-streaming behavior. Anthropic and Bedrock
adapters accept text and function tools; unsupported modalities/parameters receive an
explicit, receipted 400. OpenAI and Azure also proxy embeddings and legacy completions.
The other adapters reject those endpoints explicitly. Model-specific API restrictions
still apply. No live cloud credential or paid call is needed for the provider tests or
Compose walkthrough.

`fallbacks` is an ordered preference: put a local route first for local preference,
or a cloud route first for cloud preference. Chat, Messages, Responses, and synchronous
batch items try the next permitted route on upstream overload (429/5xx), connection
failure, or an open circuit. Ordinary 4xx errors never fall back; streaming fallback
stops once output begins. Gateway-wide load shedding still returns 503 before routing.
Embeddings and legacy completions retain single-route behavior.

Set `data_classification: confidential` in an inference body, or send
`X-Data-Classification: confidential`. Tenant `dataClassification` is a floor: neither
a body nor a header can lower it. Values are `public`, `internal`, `confidential`, and
`restricted`; the last two permit **only local routes**, including fallback, canary,
shadow, and cache selection. If no eligible local route can serve the request, the
gateway returns `403 data_classification_denied` with a hash-chained refusal receipt.
Stored Responses and uploaded batch files/jobs preserve the floor during chaining and
replay. Bind tenant identity to a key record or verified JWT claim for this to be a
tenant security boundary.

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
