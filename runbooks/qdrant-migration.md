# Qdrant Collection Migration: Dry Run And Rollback

Use this runbook when changing the RAG vector store: a new embedding model or dimension count, a
re-chunking pass, or a knowledge refresh. The RAG service reads a fixed collection name plus a
logical `collectionVersion`, so migrations are done by writing a new collection version and cutting
over, never by mutating the live collection in place.

See [Vector RAG](vector-rag.md) for the steady-state profile and
[scripts/rag-ingest.py](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/scripts/rag-ingest.py) for the ingestion job this runbook drives.

## Key Settings

| Setting | Env / value | Role |
| --- | --- | --- |
| Collection | `QDRANT_COLLECTION` (`retrieval.vectorStore.collection`) | Stable Qdrant collection name. |
| Version | `QDRANT_COLLECTION_VERSION` (`retrieval.vectorStore.collectionVersion`) | Logical version stamped on every point and filtered on retrieval. |
| Dimensions | `QDRANT_VECTOR_DIMENSIONS` (`retrieval.vectorStore.dimensions`) | Must match the embedding provider's output size. |
| Embedding | `RAG_EMBEDDING_PROVIDER` / `RAG_EMBEDDING_MODEL` | Provider whose vectors are written. |

Each ingested point's `payload.collection_version` records the version, so multiple versions can
coexist in one collection and retrieval filters to the active one.

## 1. Dry Run (no writes)

Validate the source manifest, embedding provider, and chunk plan without touching Qdrant. The
`--check` mode loads and validates the manifest, builds chunks, and prints a summary:

```bash
python3 scripts/rag-ingest.py --check \
  --source platform/rag/sources/manifest.yaml \
  --collection "$QDRANT_COLLECTION" \
  --collection-version v2 \
  --dimensions 384 \
  --embedding-provider hash
```

Confirm the printed `sources`, `documents`, and `chunks` counts match expectations and that
`dimensions` equals the embedding provider's output size. A dimension mismatch against an existing
collection is rejected at write time by `ensure_collection`, so catch it here first.

## 2. Write The New Version

Run the ingestion against Qdrant. `ensure_collection` creates the collection if missing and refuses
to write if the existing collection's dimensions differ from `--dimensions`:

```bash
python3 scripts/rag-ingest.py --write \
  --source platform/rag/sources/manifest.yaml \
  --qdrant-url "$QDRANT_URL" \
  --collection "$QDRANT_COLLECTION" \
  --collection-version v2 \
  --dimensions 384
```

The previous version (`v1`) is untouched, so retrieval keeps serving it until cutover.

## 3. Cut Over

Point the RAG service at the new version and roll the deployment:

```yaml
# deploy/clusters/<env>/values/rag-service.yaml
retrieval:
  vectorStore:
    collectionVersion: v2
```

```bash
make sync                 # or helm upgrade for direct-apply installs
make rag-smoke            # confirm grounded retrieval returns v2 results
```

A changed dimension count also requires a new collection name (Qdrant collections are
fixed-dimension); set both `collection` and `collectionVersion` and re-run from step 1.

## 4. Rollback

Because the old version was never overwritten, rollback is a values revert:

```yaml
retrieval:
  vectorStore:
    collectionVersion: v1
```

```bash
make sync
make rag-smoke
```

Retrieval immediately filters back to `v1`. Reclaim the abandoned `v2` points only after the
rollback is confirmed stable, by deleting them through the Qdrant API filtered on
`collection_version == v2`.

## Verification

- Dry run prints the expected chunk and dimension counts.
- `make rag-smoke` returns grounded results after cutover and after any rollback.
- Retention and source-approval metadata on the new points match the
  [data retention](data-retention.md) and source-manifest policy.
