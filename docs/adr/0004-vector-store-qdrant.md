# 0004. Vector store: Qdrant

- Status: Accepted
- Date: 2026-07-01
- Deciders: Platform maintainer

## Context

The RAG service needs two retrieval modes from one codebase: a dependency-free lexical retriever that
runs in the laptop and CI lab, and a vector profile for customer knowledge bases that supports dense
similarity search with metadata filtering. The vector store has to run as a self-hosted workload
inside the same Kubernetes operating model (chart, policies, PVC encryption attestation, GitOps app),
stay provider-neutral, and expose a query API the RAG service can call directly.

## Decision

Use Qdrant as the optional self-hosted vector store, behind a retriever the RAG service can run with
or without it.

- The RAG service defines a `QdrantRetriever` alongside the default lexical retriever in
  [`src/rag-service/app/retriever.py`](../../src/rag-service/app/retriever.py). The Qdrant path
  bootstraps a collection, embeds queries, over-fetches dense candidates, and applies a hybrid
  rerank that blends the dense cosine score with lexical query-term overlap (`lexical_weight`,
  default `0.5`; `0` reproduces pure dense ranking).
- Retrieval supports a metadata filter combining a collection version and a classification allowlist,
  so tenants can scope what a query may match.
- Qdrant ships as its own chart,
  [`deploy/charts/qdrant-vector-store`](../../deploy/charts/qdrant-vector-store) (`appVersion`
  `1.18.1`), described as an "optional local vector store profile," and as the `qdrant-vector-store`
  Argo CD application in the `vector` namespace
  ([`deploy/clusters/local/apps.yaml`](../../deploy/clusters/local/apps.yaml)).
- The collection migration procedure is documented in
  [`runbooks/qdrant-migration.md`](../../runbooks/qdrant-migration.md).

## Consequences

- The default lab needs no vector database: the lexical retriever keeps the quickstart light, and the
  Qdrant profile is opt-in for customers who need dense retrieval.
- Qdrant runs as a first-class platform workload: it is subject to the same Kyverno policies, carries
  the `platform.ai/encryption-at-rest` PVC attestation
  (see [0002](0002-policy-engine-kyverno.md)), and reconciles through Argo CD like everything else.
- The hybrid score is a deliberate design choice, not just a wrapper over Qdrant's search: lexical
  overlap materially improves ranking under the default hashed-vector embedding, which keeps the
  profile usable before a customer wires a real embedding model.
- Running a vector database is now an operational responsibility (storage, backup, version
  migration). The migration runbook exists precisely because collection schema/version changes are
  not free.

## Alternatives considered

- **pgvector (Postgres extension).** Attractive when a team already operates Postgres and wants
  vectors next to relational data. Rejected as the default because it would add a Postgres dependency
  the kit does not otherwise need, and the RAG service wants a purpose-built vector query API with
  native filtering rather than SQL-over-vectors.
- **Milvus.** High-scale, feature-rich vector database. Rejected as the default for being heavier to
  operate than the kit's "optional profile" goal warrants; a single maintainer can keep one Qdrant
  chart current more cheaply than a multi-component Milvus deployment.
- **Weaviate.** Capable vector database with a built-in module ecosystem. A reasonable alternative;
  Qdrant was chosen for a simple single-binary deployment that fits one chart and one PVC, and a
  query API the retriever maps onto cleanly. The choice is not a claim that Weaviate is unsuitable.
- **A managed/SaaS vector store.** Rejected because it moves tenant knowledge-base data and the
  retrieval control point outside the customer-owned boundary, which contradicts the local-first,
  provider-neutral premise of the kit.
