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

Edit `deploy/charts/rag-service/values.yaml` or an environment-specific values file:

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

    src/inference-gateway/.venv/bin/python scripts/rag-ingest.py \
      --source platform/rag/sources/platform-knowledge.yaml \
      --backend qdrant \
      --collection-version v1 \
      --check

Write to Qdrant only after source ownership, classification, retention class, and embedding model have been approved:

    QDRANT_URL=http://qdrant-vector-store.vector.svc.cluster.local:6333 \
    src/inference-gateway/.venv/bin/python scripts/rag-ingest.py \
      --source platform/rag/sources/platform-knowledge.yaml \
      --backend qdrant \
      --collection-version v1 \
      --write

The Helm chart can run an optional ingestion Job when `sourceManifest.enabled=true` and `ingestion.enabled=true`.
The service stores `collection_version` in each Qdrant point and filters queries by `retrieval.vectorStore.collectionVersion`, which lets operators stage re-ingestion or embedding-model migrations in the same collection without mixing old and new vectors.

## Per-Tenant Isolation

Per-tenant retrieval isolation is **enabled by default** (`RAG_RETRIEVAL_TENANT_ISOLATION_ENABLED` defaults `true`). When enabled, a query returns only documents whose tenant field (`RAG_RETRIEVAL_TENANT_FIELD`, default `owner`) equals the caller's `X-Sandbox-ID`.

- **Both backends enforce it.** The Qdrant path appends an `owner` match to the query filter; the lexical path stamps its on-disk corpus with the service's default sandbox id (`DEFAULT_SANDBOX_ID`) and applies the same owner scoping.
- **Fail-closed, always.** A tenant with no matching documents gets none. A request that does not *explicitly* send `X-Sandbox-ID` (so the service fell back to `DEFAULT_SANDBOX_ID`) is not treated as a tenant assertion and returns no documents rather than the default sandbox's corpus. A missing-tenant query never issues an unfiltered search. Isolation is never fail-open.
- **Ingest must stamp the owner.** For isolation to match anything, each source's `owner` in the `RagSourceManifest` must be the owning tenant/sandbox id (see `scripts/rag-ingest.py`). The bootstrap knowledge corpus is stamped `owner=platform-team`; with isolation on, those platform documents are invisible to tenant-scoped queries by design (the service logs this at startup).

The bundled **local lexical lab** ships with isolation **off** (`retrieval.tenantIsolation.enabled: false`) because it serves shared platform documents to every caller as a single tenant. **Multi-tenant / customer profiles must set `retrieval.tenantIsolation.enabled: true`** (the customer values file does; confirm it when handing off).

**Trust boundary and remaining work.** The tenant id is the client-asserted `X-Sandbox-ID` header, verified only insofar as a trusted upstream sets it — the inference gateway derives `X-Sandbox-ID` from a verified JWT tenant claim (`JWT_TENANT_CLAIM`, rejecting mismatches), or a workspace egress proxy stamps it. A direct caller holding the shared RAG API key can still assert another tenant's id. **TODO (roadmap — "RAG Hardening"): give the RAG service its own audience-bound token verification (JWKS/audience validation, mirroring the gateway's `jwt_auth`) so the tenant is derived from a verified claim on the RAG service itself rather than trusting the header.** Until then, keep the RAG service reachable only via the gateway or a header-stamping proxy in multi-tenant clusters, and treat the shared API key as an auth-N control, not a tenant boundary.

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
