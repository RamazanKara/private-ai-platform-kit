# Runbook: Asynchronous Files + Batch API

Operational guide for the OpenAI-compatible async batch subsystem (ADR 0011): the
`/v1/files` and `/v1/batches` endpoints on the inference gateway plus the `batch-processor`
worker that drains the queue.

## What it is

Submit many requests as one JSONL file and have them processed asynchronously under the same
governance as live traffic, then retrieve the results later. This is distinct from the
synchronous `POST /v1/batch-inference` fan-out (which returns inline within one request).

- The **gateway** serves `/v1/files` (upload/get/content/delete/list) and `/v1/batches`
  (create/get/cancel/list). It stores JSONL blobs in the object store and job records + the
  work queue in Redis.
- The **batch-processor** worker (the gateway image run with `python -m app.batch_worker`)
  claims a batch, replays each input line back through the gateway's governed endpoint, writes
  the output/error files, and advances the batch to a terminal state.

Because items are replayed through the gateway, every batched request passes the model
allowlist, admission caps, prompt-secret policy, per-tenant budget, the output guardrail, tenant
isolation, and the audit chain, identically to live traffic.

## Enabling it

The feature is off by default. To enable (Helm values on the `inference-gateway` chart):

```yaml
batch:
  enabled: true
  objectStore:
    backend: s3            # s3 is required for a multi-pod cluster (blobs must be shared)
    s3:
      endpointUrl: http://minio.storage.svc.cluster.local:9000
      bucket: platform-batches
      accessKeyId: ...
      existingSecret: { name: batch-s3, key: s3-secret-access-key }
  store:
    backend: redis
    redisUrl: redis://budget-redis.budget.svc.cluster.local:6379/2
  worker:
    enabled: true
    # When gateway auth is on, give the worker a service key the gateway allowlists:
    apiKey: { existingSecret: { name: batch-worker, key: batch-worker-api-key } }
```

The object store (MinIO or cloud S3) is operator-provided. The `filesystem`/`memory` object
backends are for single-process local runs only; they are **not** shared across the gateway and
worker pods, so a cluster must use `s3`.

## Normal flow

```bash
# 1. Upload the input file (one JSON request per line: custom_id, method, url, body).
FILE=$(curl -fsS -H "X-API-Key: $KEY" -F purpose=batch -F file=@requests.jsonl \
  "$GATEWAY/v1/files" | jq -r .id)
# 2. Create the batch.
BATCH=$(curl -fsS -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d "{\"input_file_id\":\"$FILE\",\"endpoint\":\"/v1/chat/completions\"}" \
  "$GATEWAY/v1/batches" | jq -r .id)
# 3. Poll status until terminal (completed / failed / expired / cancelled).
curl -fsS -H "X-API-Key: $KEY" "$GATEWAY/v1/batches/$BATCH" | jq '{status, request_counts}'
# 4. Retrieve the output (and error) file content.
OUT=$(curl -fsS -H "X-API-Key: $KEY" "$GATEWAY/v1/batches/$BATCH" | jq -r .output_file_id)
curl -fsS -H "X-API-Key: $KEY" "$GATEWAY/v1/files/$OUT/content"
```

Status lifecycle: `validating → in_progress → finalizing → completed`, or `failed` / `expired`,
or `cancelling → cancelled`. Successful (2xx) items land in the `output_file_id` file; everything
else lands in the `error_file_id` file. Partial completion is normal.

## Cancellation and expiry

- `POST /v1/batches/{id}/cancel` flips the batch to `cancelling`; the worker finalizes it to
  `cancelled` at its next item boundary (best-effort; in-flight items may still complete).
- A batch not finished within its `completion_window` (default `24h`, honored as an expiry
  bound) is swept to `expired` by the worker's reaper.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Batches stay `validating`, never `in_progress` | The `batch-processor` Deployment is running (`kubectl get deploy -l app.kubernetes.io/component=batch-processor`) and `batch.worker.enabled` is true. Its logs (`kubectl logs`) show `batch-processor started`. |
| Worker logs "BATCH_API_ENABLED is false" | The worker Deployment did not get `BATCH_API_ENABLED=true`; it is set by the chart when `batch.enabled` is true. |
| Items all fail with 401/403 in the error file | The worker's service key (`batch.worker.apiKey`) is missing or not allowlisted by the gateway; or a JWT tenant mismatch. |
| Batch fails with "input file content is missing" | The gateway and worker are not pointed at the same object store, or the input file was deleted. Use `s3` (shared) in-cluster, not `filesystem`. |
| A batch is stuck `in_progress` after a worker crash | The reaper re-queues it after `BATCH_WORKER_RECLAIM_SECONDS` (default 300s); another worker replica picks it up. Re-delivery is idempotent. |

Batched requests appear in the gateway audit log like any other request, so the tamper-evident
audit chain and `make audit-verify` cover them (see [audit-chain.md](audit-chain.md)).
