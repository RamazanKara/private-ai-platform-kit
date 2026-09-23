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
- Serving profile values: `deploy/clusters/customer/values/vllm-embeddings.yaml`,
  `deploy/clusters/customer/values/inference-gateway.yaml`
- Gateway allowlist: customer

## Provenance

- Source: huggingface (`https://huggingface.co/BAAI/bge-small-en-v1.5`)
- Revision: `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`
- Immutable reference:
  `huggingface://BAAI/bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a#sha256:a9b5338e6e96683e2eb17c4aa028b30b42f2b30a0cf7081e3fd80f864fefc421`
- Digest: `sha256:a9b5338e6e96683e2eb17c4aa028b30b42f2b30a0cf7081e3fd80f864fefc421`
  (scope: artifact-manifest)
- Weight inventory: [bge-small-en-v1.5.json](../artifacts/bge-small-en-v1.5.json)
- Verification: `huggingface-safetensors-manifest` via `make model-provenance-verify`
- License: mit

The inventory records each safetensors file's upstream SHA-256 and size at the pinned
commit. Verification reproduces this inventory from upstream metadata without downloading
weights. Verify the actual downloaded files against the inventory before production use.

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
- Upstream metadata verification does not inspect the customer's model cache; verify downloaded
  files against the inventory before production.

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
