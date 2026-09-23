# RAG service

This FastAPI service retrieves knowledge and returns documents, context, and
grounded messages. Applications send those messages to the inference gateway for
generation. The local profile uses lexical retrieval; customer deployments can
use Qdrant, an embedding endpoint, and an optional reranker.

Use the [developer workflow](../../docs/development.md) for environment setup and
the [RAG runbook](../../runbooks/rag-service.md) for deployment and operation.

## Code map

| Area | Modules |
| --- | --- |
| Application assembly, middleware, request models, and HTTP routes | [main.py](app/main.py) |
| Validated environment settings | [settings.py](app/settings.py) |
| Lexical and Qdrant retrieval, tenant filtering, and context assembly | [retriever.py](app/retriever.py) |
| Embedding providers and tokenization | [embeddings.py](app/embeddings.py) |
| Optional reranking | [reranker.py](app/reranker.py) |
| Source manifests, chunking, and ingestion | [ingest.py](app/ingest.py) |
| Authentication and request limits | [jwt_auth.py](app/jwt_auth.py), [body_limit.py](app/body_limit.py) |
| Audit chains and tracing | [audit.py](app/audit.py), [tracing.py](app/tracing.py) |

Keep retrieval changes independent of HTTP handling when possible. Tenant filtering
must apply to both lexical and vector retrieval. Use temporary document directories
and fake HTTP providers in tests so the suite remains independent of Qdrant and
embedding services.

## Test and run locally

From the repository root:

```bash
make test-rag
make rag-eval-check
```

The service tests prepare their own hashed development environment.
`make rag-eval-check` checks the retrieval-evaluation metrics and golden suite
configuration; it does not run a live retrieval benchmark.

Run a local lexical service over the repository's documentation:

```bash
RAG_DOCUMENT_DIR="$PWD/docs" RAG_RETRIEVAL_BACKEND=lexical \
  src/rag-service/.venv/bin/python -m uvicorn app.main:app \
  --app-dir src/rag-service --host 127.0.0.1 --port 8081 --reload
```

Open `http://127.0.0.1:8081/docs` for the local API. The server loads Markdown/text
documents at startup. Restart it after changing the corpus. This loopback example
uses environment defaults; it does not require a gateway, model, or vector store.

## Related contracts

- [API contract](../../platform/api-contracts/rag-service.openapi.json):
  regenerate with `make api-contract-update` after public route/schema changes.
- [Configuration contract](../../platform/config-contracts/rag-service.config.json):
  regenerate with `make config-contract-update` after settings or chart changes.
- [Helm chart](../../deploy/charts/rag-service/README.md) and
  [retrieval evaluation suite](../../platform/evals/rag-retrieval-suite.yaml).
- [Vector-store operation](../../runbooks/vector-rag.md) and
  [Qdrant migration](../../runbooks/qdrant-migration.md).
