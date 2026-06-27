# RAG Service Runbook

Use this runbook when coding agents or lab users need approved platform context before calling the inference gateway.

## What It Provides

The RAG service exposes:

- `GET /healthz`
- `GET /metrics`
- `GET /v1/rag/documents`
- `POST /v1/rag/query`

Queries return retrieved document excerpts, a query SHA-256 fingerprint, optional context, and OpenAI-compatible `grounded_messages` that can be passed to the inference gateway. Audit logs include query length and hash, not raw query text.

When `auth.enabled` is true, `POST /v1/rag/query` and `GET /v1/rag/documents` require `X-API-Key` or `Authorization: Bearer`. Health and metrics remain unauthenticated for Kubernetes probes and scraping.

The local profile uses lexical retrieval. Customer values can switch to the Qdrant vector-store profile with `retrieval.backend=qdrant`; see `runbooks/vector-rag.md` for storage, network, and collection operations.

## Local Validation

Deploy the local stack, then run:

    make rag-smoke

The smoke test port-forwards the RAG service, sends `X-Request-ID`, `X-Sandbox-ID`, and `traceparent`, then verifies retrieved results and grounded messages.

## Add Knowledge

Edit `charts/rag-service/values.yaml` or an environment-specific values file:

    knowledge:
      documents:
        coding-standards.md: |
          # Coding Standards
          ...

For customer clusters, prefer environment-specific values or a chart override sourced from the customer's internal Git repository.

## Source Metadata And Ingestion

Customer document ingestion starts with a `platform.ai/v1alpha1` `RagSourceManifest`:

    apiVersion: platform.ai/v1alpha1
    kind: RagSourceManifest
    spec:
      sources:
        - id: platform-docs
          source: docs
          classification: internal
          retentionClass: project-documentation
          owner: platform-team
          embeddingModel: hash-text-v1

Validate a source manifest locally without writing vectors:

    services/inference-gateway/.venv/bin/python scripts/rag-ingest.py \
      --source rag/sources/platform-knowledge.yaml \
      --backend qdrant \
      --collection-version v1 \
      --check

Write to Qdrant only after source ownership, classification, retention class, and embedding model have been approved:

    QDRANT_URL=http://qdrant-vector-store.vector.svc.cluster.local:6333 \
    services/inference-gateway/.venv/bin/python scripts/rag-ingest.py \
      --source rag/sources/platform-knowledge.yaml \
      --backend qdrant \
      --collection-version v1 \
      --write

The Helm chart can run an optional ingestion Job when `sourceManifest.enabled=true` and `ingestion.enabled=true`.
The service stores `collection_version` in each Qdrant point and filters queries by `retrieval.vectorStore.collectionVersion`, which lets operators stage re-ingestion or embedding-model migrations in the same collection without mixing old and new vectors.

## Troubleshooting

Check service health:

    kubectl -n rag get deploy,svc,pod
    kubectl -n rag logs deploy/rag-service-rag-service --tail=100

Check a query:

    kubectl -n rag port-forward svc/rag-service-rag-service 18083:8080
    curl -sS -H 'Content-Type: application/json' \
      -H 'X-Sandbox-ID: agent-lab' \
      -H "X-API-Key: ${PLATFORM_API_KEY:-local-development-only}" \
      -d '{"query":"coding agents gateway trace headers","top_k":2}' \
      http://127.0.0.1:18083/v1/rag/query | python3 -m json.tool

If results are empty, confirm the knowledge ConfigMap contains relevant `.md` or `.txt` documents and the deployment has rolled out.

If the backend is `qdrant`, also confirm the `vector` namespace service is reachable, the collection name, collection version, and vector dimensions match the RAG values, and the Qdrant PVC is bound.
