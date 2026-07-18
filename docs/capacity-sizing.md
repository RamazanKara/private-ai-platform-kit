# Capacity and sizing

The chart values are examples, not sizing recommendations. Measure the selected model and workload before setting production requests, limits, or autoscaling bounds.

## Inputs

Collect these first:

- model artifact, precision, and runtime version;
- prompt and completion length distributions;
- peak concurrent requests and arrival rate;
- latency and availability objectives;
- GPU memory and measured decode/prefill throughput;
- RAG document, chunk, vector-dimension, and growth counts;
- retention and backup requirements;
- allowed cold-start time and maximum replica cost.

`make loadtest-local` does not measure model capacity. It starts a mock OpenAI-compatible runtime. Use it for gateway-path regression tests, then run `make loadtest` against the real deployment.

## Gateway

The gateway chart can scale with KEDA on four Prometheus signals:

- total `/v1/*` request rate;
- in-flight request count;
- load-shed rate;
- p95 request latency.

The current customer values set a floor of 2 and a ceiling of 20 replicas. Treat the thresholds as initial configuration. Verify that the Prometheus queries return data in the target cluster before relying on them.

`concurrency.maxConcurrentRequests` is a per-process load-shed limit. `0` disables that limit. A higher value does not add model capacity; it only lets more work reach the runtime. Size the gateway and runtime together and watch 503 responses, queue depth, and tail latency.

## vLLM

The NVIDIA customer example currently sets:

| Setting | Value |
| --- | --- |
| Replica floor/ceiling | 2 / 8 with KEDA |
| GPUs per replica | 4 |
| Tensor parallel size | 4 |
| Maximum model length | 262,144 tokens |
| Shared model cache | 400 GiB, `ReadWriteMany` |

This profile assumes a large coding model and a cluster that can place a four-GPU pod. For another model, change the values together:

- GPU count must match the tensor-parallel layout;
- weights, KV cache, activations, and runtime overhead must all fit;
- long context can consume more memory than the weights;
- each replica must fit the node topology unless a separately installed multi-node operator is used;
- scale-up time includes scheduling and model loading.

KEDA uses `vllm:num_requests_waiting` in the provided values. Confirm the metric name against the deployed vLLM version and keep enough floor capacity for the accepted cold-start time.

## Qdrant and RAG

The bundled Qdrant chart supports one replica and a single PVC. It is a reference footprint, not an HA topology. Estimate raw vector storage as:

```text
vectors * dimensions * 4 bytes
```

Then add payload, HNSW index, segment, compaction, snapshot, and growth headroom. Measure memory and query latency after loading representative data. The customer values currently request a 100 GiB PVC; that number alone says nothing about supported document count.

Embedding dimensions in the RAG values must match the selected embedding model and collection. Changing them is a collection migration, not an in-place tuning change.

## Redis, object storage, and logs

The bundled budget Redis is a single, non-persistent development store. It is also used by optional gateway state. A restart can lose counters or cached/stateful data depending on the enabled features. Use the [external stores runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/external-managed-stores.md) before relying on it for a multi-replica customer deployment.

Files and asynchronous batches require an object store when enabled. Include upload limits, object retention, failed-batch output, and cleanup in the storage estimate.

Prometheus, logs, and audit exports grow with traffic and retention. The customer profile expects the operator to supply those systems.

## Validation loop

1. Render the exact values and confirm pod placement against real node shapes.
2. Load the exact model and representative RAG data.
3. Warm the runtime, then test sustained and burst traffic separately.
4. Record successful throughput, queue depth, GPU memory, p50/p95/p99 latency, errors, and cold-start time.
5. Test one dependency failure and one replica loss at peak load.
6. Set replica floors from the accepted baseline and ceilings from both capacity and cost limits.
7. Repeat after model, runtime, context, quantization, or hardware changes.

The [GPU capacity runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/gpu-capacity.md) has scheduling diagnostics. The [SLO runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/slo-error-budget.md) covers acceptance thresholds.
