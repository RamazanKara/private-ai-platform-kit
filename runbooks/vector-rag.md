# Vector RAG Runbook

Use this runbook when the customer knowledge base is too large for the bundled lexical RAG profile or when coding agents need stable semantic retrieval across platform, repository, and incident context.

## Profile Contract

The default local profile keeps `retrieval.backend=lexical` so a workstation can run without extra services. Customer values set `retrieval.backend=qdrant` and point the RAG service at `qdrant-vector-store.vector.svc.cluster.local:6333`.

The Qdrant profile provides:

- a dedicated `vector` namespace through GitOps or local direct apply
- a pinned Qdrant deployment with Service, ServiceAccount, NetworkPolicy, PDB, and optional PVC
- RAG env vars for backend, URL, collection, collection version, timeout, vector dimensions, and bootstrap behavior
- deterministic local hashed embeddings for lab validation without calling an external embedding API
- optional OpenAI-compatible embedding endpoints owned by the customer
- optional bootstrap from the approved RAG knowledge ConfigMap
- optional manifest-driven Qdrant ingestion Job with classification, retention, owner, and embedding metadata

For production customer knowledge, keep the same API and network contract but replace the lab embedding strategy with the customer's approved embedding model, document pipeline, and collection lifecycle.

Embedding settings live under the RAG chart:

    retrieval:
      embedding:
        provider: openai-compatible
        baseUrl: http://customer-embedding.embedding.svc.cluster.local:8080
        model: bge-small-private
      vectorStore:
        collectionVersion: v1
        dimensions: 384

Keep `retrieval.vectorStore.dimensions` exactly aligned with the embedding endpoint response size. Increment `retrieval.vectorStore.collectionVersion` when re-ingesting with an incompatible chunking, embedding model, or metadata policy so old and new points do not mix during migration.

## Customer Sizing

Review `clusters/customer/values/qdrant-vector-store.yaml` before handoff:

    persistence:
      enabled: true
      size: 100Gi

    resources:
      requests:
        cpu: "1"
        memory: 4Gi
      limits:
        cpu: "4"
        memory: 16Gi

Set storage class, size, resource requests, backup policy, and collection count to the customer's document volume and SLO. Keep the RAG `retrieval.vectorStore.dimensions` value aligned with the embedding vector size and track collection migrations through `retrieval.vectorStore.collectionVersion`.

## Validation

Render and validate the charts:

    helm template validate-qdrant charts/qdrant-vector-store --values clusters/customer/values/qdrant-vector-store.yaml
    helm template validate-rag charts/rag-service --values clusters/customer/values/rag-service.yaml
    make production-check

After deployment, check Qdrant and RAG:

    kubectl -n vector get deploy,svc,pvc
    kubectl -n rag get deploy rag-service-rag-service
    kubectl -n rag logs deploy/rag-service-rag-service --tail=100

The RAG health endpoint reports the selected backend and collection metadata:

    kubectl -n rag port-forward svc/rag-service-rag-service 18083:8080
    curl -sS http://127.0.0.1:18083/healthz | python3 -m json.tool

Run a dry ingestion check before writing:

    src/inference-gateway/.venv/bin/python scripts/rag-ingest.py \
      --source platform/rag/sources/platform-knowledge.yaml \
      --backend qdrant \
      --collection-version v1 \
      --check

## Operations

If queries return `vector_store_unavailable`, inspect:

- Qdrant pod readiness and PVC binding
- RAG NetworkPolicy egress to namespace `vector` on TCP 6333
- DNS egress to `kube-system` on port 53
- matching collection name, collection version, and vector dimensions
- Qdrant logs for collection creation or upsert errors

Do not load unreviewed private repository or incident data into the vector store. Treat embedded content as customer confidential data and align backup, retention, and deletion procedures with `platform/governance/data-retention.yaml`.
