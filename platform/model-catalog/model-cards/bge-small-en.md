# Model Card: BAAI/bge-small-en-v1.5

This card restates the governed facts for `BAAI/bge-small-en-v1.5`. The sources of truth are
`platform/model-catalog/models.yaml`, `platform/governance/model-provenance.yaml`, and
`platform/model-catalog/promotion-requests/bge-small-embedding-approved.yaml`. If this card and
those files disagree, the YAML is authoritative.

## Identity

- Model id: `BAAI/bge-small-en-v1.5`
- Runtime: vllm
- Modality: embedding
- Owner: platform-team
- Lifecycle status: approved
- Stage: customer-lab

## Intended use

Reference embedding model for governed RAG retrieval. It produces 384-dimensional vectors, matching
the RAG service default vector dimensions, and replaces the demo hashed-vector default. It is
allowlisted in the `customer` gateway (`deploy/clusters/customer/values/inference-gateway.yaml`)
and is consumed by the RAG service via its OpenAI-compatible embedding provider
(`retrieval.embedding.provider=openai-compatible`), proxied through the gateway `/v1/embeddings`
route. Its purpose is to bring retrieval-quality models under the same catalog and provenance
governance as generation models.

## Out-of-scope / not approved for

- Text generation. It is an embedding model: it produces vectors, not completions. The
  `maxCompletionTokens: 1` value is nominal.
- The local profile. It is allowlisted only in `customer`.
- Drop-in production use without confirming the vector dimensions match the live Qdrant collection
  (384-dim) and re-running retrieval evals against the real corpus.

## Runtime and serving profile

- Serving runtime: vLLM embedding mode (`--task embed`) or a TEI sidecar
- Accelerators: nvidia, cpu
- Context window: 512 tokens
- Gateway admission: maxPromptChars 2048, maxCompletionTokens 1 (nominal)
- Serving profile values: `deploy/clusters/customer/values/inference-gateway.yaml`
- Gateway allowlist: customer

## Provenance

- Source: huggingface (`https://huggingface.co/BAAI/bge-small-en-v1.5`)
- Revision: `main` (pin to a specific Hugging Face commit before production)
- Immutable reference:
  `huggingface://BAAI/bge-small-en-v1.5@sha256:99aab2ead8654ba801d2a01b188b13cc119e5d2d10880e8d1ee4a1315aa99e72`
- Digest: `sha256:99aab2ead8654ba801d2a01b188b13cc119e5d2d10880e8d1ee4a1315aa99e72`
  (scope: source-reference)
- Verification: `customer-model-store` — `huggingface-cli scan-cache --dir /models`
- License: mit

The bundled digest is a deterministic source-reference over the model reference string
(`printf 'huggingface://BAAI/bge-small-en-v1.5' | sha256sum`), not a model-artifact checksum.
Replace it with the customer's pinned model-store artifact revision and checksum before production
use.

## Data classification and risk

- Data classification: internal
- Risk tier: low
- Prompt logging: redacted
- External network required: false
- Requires GPU: false

## Known limitations

- Embedding-only: no generation capability.
- Short 512-token context window; inputs must be chunked accordingly for retrieval.
- 384-dimensional output is fixed and must match the configured Qdrant collection dimensions or
  retrieval will fail.
- Bundled provenance is a source-reference digest, not an artifact checksum; pin and re-verify
  against a private model store before production.

## Evaluation evidence

- Eval suite: `platform/evals/rag-retrieval-suite.yaml` (labeled golden-query retrieval set over
  the shipped platform knowledge documents)
- Eval summary: `results/evals/sample-summary.md`
- Load-test summary: `results/loadtest/sample-summary.md`
- Security workflow: `.github/workflows/ci.yml`

## Approval reference

- Promotion request:
  `platform/model-catalog/promotion-requests/bge-small-embedding-approved.yaml`
- Requested by: platform-team
- Approvers: model-governance-board
- Business justification: Promote a governed reference embedding model (384-dim, matching the RAG
  default vector dimensions) so retrieval-quality models receive the same catalog and provenance
  governance as generation models, replacing the demo hashed-vector default. Served via vLLM
  embedding mode or a TEI sidecar and consumed by the RAG service OpenAI-compatible embedding
  provider.
