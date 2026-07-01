# Capacity and Sizing Worksheet

This is the concrete companion to the prose in [`runbooks/gpu-capacity.md`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/gpu-capacity.md).
That runbook explains *why* GPU pods fail to schedule and gives rough VRAM/concurrency estimates;
this page turns those estimates into a repeatable worksheet you fill in from your own targets, plus a
few reference deployments with the exact values files they map to.

The numbers here are planning estimates, not guarantees. Every figure that touches GPU memory,
tokens/sec, or concurrency depends on the model, the weight precision, the KV-cache size at your
context length and batch, and the serving runtime. Treat the worksheet output as a *starting point*
and validate it with a real load test (`make loadtest-local` against the customer profile) before you
commit capacity. See the [Sizing Estimates](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/gpu-capacity.md#sizing-estimates) table in the
runbook for the underlying per-model VRAM and concurrency ranges this worksheet draws on.

All Helm defaults cited below come from `deploy/charts/*/values.yaml`; the reference deployments cite
the overlay values under `deploy/clusters/{local,customer}`.

## How to use this page

1. Write down your inputs (target RPS, peak concurrency, model, context length, vector count, budget).
2. Work top to bottom through the four worksheets: gateway, vLLM GPU, Qdrant storage, Redis.
3. Cross-check against the closest reference deployment in the worked-example table.
4. Load-test, then revise. The autoscalers (KEDA) absorb error in the *up* direction; the floors
   (`minReplicaCount`, GPU `count`) are what you must get right by hand.

## Inputs to gather first

| Input | Symbol | Where it comes from |
| --- | --- | --- |
| Target sustained request rate | `RPS` | Expected chat completions per second at peak |
| Peak simultaneous in-flight requests | `C` | Concurrent agent sessions x requests each |
| Served model | -- | A model in the gateway `allowedModels` / routing policy |
| Context length actually enabled | `L` | vLLM `model.maxModelLen` (not the model maximum) |
| Per-replica decode throughput | `T_tok` | Measured tokens/sec for the model on the chosen GPU class |
| RAG vector count and dimension | `N`, `d` | Docs x chunks; `d` = `vectorStore.dimensions` |
| Per-sandbox 24h budget | -- | Requests / prompt chars / estimated tokens per window |

You will not know `T_tok` until you measure it; the worked examples below give order-of-magnitude
anchors, but a load test is the source of truth.

## Worksheet 1 -- Gateway replicas

The inference gateway is CPU/IO-bound (it proxies to the runtime), so it scales horizontally on
**request rate**, not GPU. Two independent controls set capacity: KEDA picks the replica *count*, and
`concurrency.maxConcurrentRequests` caps in-flight work *per replica*.

### 1a. Replica count from RPS (KEDA request-rate trigger)

The gateway ScaledObject (`deploy/charts/inference-gateway/templates/scaledobject.yaml`) scales on this
Prometheus query, with the per-replica target set by `keda.threshold`:

```
metricName: inference_gateway_requests_per_second
query:      sum(rate(inference_gateway_requests_total{route="/v1/chat/completions",status="200"}[2m]))
threshold:  keda.threshold        # successful chat req/s each replica should carry
```

KEDA divides the cluster-wide rate by `threshold` to get the desired replica count, then clamps it to
`[keda.minReplicaCount, keda.maxReplicaCount]`:

```
desired_replicas = clamp( ceil( RPS / keda.threshold ),
                          keda.minReplicaCount,
                          keda.maxReplicaCount )
```

- Chart default `keda.threshold` is `"10"` req/s per replica, with `min/max = 1/5`
  (`deploy/charts/inference-gateway/values.yaml`).
- The customer overlay raises this to `threshold: "25"`, `min/max = 2/20`
  (`deploy/clusters/customer/values/inference-gateway.yaml`).

Set `keda.minReplicaCount` to cover your *baseline* RPS without waiting for a scale-up, and
`keda.maxReplicaCount` to your *peak* divided by `threshold`, rounded up. The threshold is the knob
that trades replica count against per-replica load; lower it if p99 latency degrades before CPU
saturates.

> The query counts only `status="200"` on `/v1/chat/completions`. Load-shed 503s and embeddings
> traffic are deliberately excluded, so the autoscaler tracks *served* chat demand, not rejected or
> non-chat calls. Size embeddings-heavy RAG ingestion separately.

### 1b. Per-replica concurrency cap

`concurrency.maxConcurrentRequests` bounds in-flight requests on a single gateway pod; requests beyond
it are **shed with 503, not queued** (`app/main.py`: when `inflight >= max_concurrent_requests` the
request is rejected). The default is `0` (unlimited) -- the gateway leans on the runtime and the
budget store for back-pressure.

Set it to protect the runtime from overload while still serving your peak:

```
maxConcurrentRequests  >=  ceil( C / desired_replicas )      # so peak concurrency does not 503
maxConcurrentRequests  <=  what one vLLM/Ollama replica can serve at acceptable latency
```

If those two bounds conflict, you need more gateway replicas (raise `keda.maxReplicaCount`) or more
runtime capacity (Worksheet 2) -- not a higher cap. Related per-call admission limits live under
`admission.*` (`maxMessages`, `maxPromptChars`, `maxCompletionTokens`) and the batch ceiling is
`concurrency.maxBatchRequests` (default `32`).

The gateway resource defaults are modest (`requests 100m / 128Mi`, `limits 500m / 512Mi`); they rarely
need raising because you scale out rather than up.

## Worksheet 2 -- vLLM GPU sizing

vLLM is GPU-bound, so CPU% is the wrong autoscaling signal (the chart's CPU HPA is a disabled
placeholder for exactly this reason). The hand-sized inputs are: **GPUs per replica**, **tensor-parallel
size**, and **context length**; KEDA then scales *replicas* on queue depth.

### 2a. GPUs per replica and tensor-parallel size

Pick the GPU class and per-replica GPU count from the model's weight + KV-cache footprint. Use the
[runbook's per-model table](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/gpu-capacity.md#sizing-estimates) as the anchor, then set:

```
accelerator.count          = GPUs per replica  (must fit on one node for tensor parallelism)
extraArgs: --tensor-parallel-size  = accelerator.count   (single-node TP; keep them equal)
```

The default profile serves `Qwen/Qwen3-Coder-Next` with `accelerator.count: 4` and
`--tensor-parallel-size 4` (`deploy/charts/vllm/values.yaml`, `extraArgs`). Tensor parallelism shards
one model across the GPUs of a single node; if the model is too large for any one node, you need
**pipeline parallelism across nodes** via the LeaderWorkerSet topology -- see
[Multi-Node Serving](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/gpu-capacity.md#multi-node-serving-models-larger-than-one-node). To fit
a smaller cluster, reduce `accelerator.count`, `--tensor-parallel-size`, `model.maxModelLen`, or the
model itself.

### 2b. KV-cache memory and context length

At long context, **KV-cache, not weights, dominates GPU memory**. Per-token KV-cache grows with the
context length you actually enable, so size headroom for `model.maxModelLen`, not the model maximum:

```
GPU_mem_per_replica  >=  weight_VRAM (precision-dependent)
                      +  KV_cache(model.maxModelLen, peak_batch)
                      +  runtime/activation overhead
```

The default profile sets `model.maxModelLen: "262144"` (262k tokens). That is a large window -- if your
agents do not need it, lowering it is the single most effective way to cut GPU memory and raise
concurrency. The runbook notes 4-bit quantization roughly halves-to-quarters weight VRAM but can hurt
quality; validate evals before relying on it for coding agents.

Container memory requests (host RAM, not VRAM) default to `requests 32Gi / limits 64Gi` and rarely
need tuning relative to GPU memory.

### 2c. Replica count from throughput (KEDA queue trigger)

vLLM replicas scale on request-queue depth, not RPS. The vLLM ScaledObject query and threshold are:

```
query:      avg(vllm:num_requests_waiting)     # average backlog across replicas
threshold:  keda.threshold                     # queued requests per replica before scaling up
```

```
desired_vllm_replicas = clamp( ceil( avg_queue_depth / keda.threshold ),
                               keda.minReplicaCount, keda.maxReplicaCount )
```

vLLM `keda` is disabled by default in the chart (it is environment-specific). The customer NVIDIA
profile enables it with `threshold: "5"`, `min/max = 2/8`
(`deploy/clusters/customer/values/vllm-nvidia.yaml`). To translate your throughput target into replica
count, estimate sustained capacity per replica as roughly `T_tok / avg_tokens_per_request` requests/sec,
then provision enough replicas that the queue stays below `threshold` at peak. A persistent backlog
above `threshold` means add replicas (or GPUs); an always-empty queue means you over-provisioned.

> Cold starts are expensive: a 100+ GB model load can take minutes (the startup probe grace window is
> `60 x 10s = 10 min`). Pair scale-to-zero (`minReplicaCount: 0`) with the persistent weight cache
> (`cache.persistence`) and the generous `cooldownPeriod: 300` so replicas are not churned. For
> multi-replica weight sharing you need a ReadWriteMany storage class (the customer profile sets
> `cache.persistence` `size: 400Gi`, `accessMode: ReadWriteMany`); with ReadWriteOnce keep
> `replicaCount: 1` or pre-bake weights into the image.

## Worksheet 3 -- Qdrant vector storage

Qdrant is the dense half of the hybrid RAG backend. It is **single-instance only** (enforced by
`values.schema.json`; raising `replicaCount` is unsafe on the shared RWO PVC), so sizing here is about
the PVC, not replicas.

Estimate raw vector bytes, then add generous overhead for the HNSW index, payloads, and segment
write-amplification:

```
raw_vectors   =  N (vectors) x d (dimensions) x 4 bytes   # float32
provisioned   ~=  raw_vectors x 3-5  + payload + headroom  # HNSW graph, payloads, segments, growth
```

- `d` defaults to `384` (`rag-service` `retrieval.vectorStore.dimensions`), matching small
  bge-class / hash embeddings. A `bge-small` profile keeps `d=384`; larger embedding models raise `d`
  and therefore storage linearly.
- Worked: 1M vectors x 384 x 4 bytes is ~1.5 GB raw; with index + payload + headroom the chart's
  default `persistence.size: 20Gi` (`deploy/charts/qdrant-vector-store/values.yaml`) comfortably
  covers low-millions of 384-dim vectors. The customer overlay provisions `100Gi`
  (`deploy/clusters/customer/values/qdrant-vector-store.yaml`) for larger corpora.

Resource defaults are `requests 250m / 512Mi`, `limits 2 CPU / 4Gi`; the customer overlay raises limits
to `4 CPU / 16Gi`. HNSW search is memory-sensitive, so raise the memory limit before the CPU limit as
the collection grows. Retrieval over-fetches `candidateMultiplier x topK` (default `4 x`) dense
candidates before reranking, so search cost scales with `topK` and the multiplier, not just `N`.

## Worksheet 4 -- Budget Redis sizing

`budget-redis` is the shared store for per-sandbox cumulative budgets and the short-window rate-limit
counters (gateway `budget.backend: redis`). It holds small counter keys per sandbox per window, so it
is tiny -- the chart defaults to `requests 50m / 64Mi`, `limits 250m / 256Mi`
(`deploy/charts/budget-redis/values.yaml`).

```
keys  ~=  (sandboxes) x (a handful of counters each) x (active windows)
```

With `windowSeconds: 86400` (24h) and counters that are a few hundred bytes each, even thousands of
sandboxes stay well under the default `256Mi` limit. Raise the memory limit only if you run many
thousands of concurrent sandboxes or shorten the window dramatically. The budget *limits themselves*
(`requestLimit`, `promptCharLimit`, `estimatedTokenLimit`) are policy knobs that gate traffic, not
Redis sizing inputs -- but a high token budget paired with high `keda.maxReplicaCount` is what lets a
busy tenant actually reach the throughput you sized in Worksheets 1-2.

> The in-memory budget backend (`backend: memory`, the chart default) is per-pod and does not
> aggregate across gateway replicas. Once `keda.minReplicaCount > 1`, switch to `backend: redis` (as
> the local and customer overlays do) or budgets are enforced per-pod, not per-sandbox.

## Worked examples

Three reference points, mapped to the values files they correspond to. The throughput and concurrency
columns are planning anchors; measure before committing.

| Profile | Gateway (`keda` min/max, threshold) | Runtime (replicas, GPUs/replica, TP) | Context | Qdrant PVC | Redis | Values files |
| --- | --- | --- | --- | --- | --- | --- |
| Laptop / local (kind) | min 1 / max 3 (chart default threshold 10/s) | Ollama 1 replica, **no GPU** (`vLLM replicaCount: 0`) | n/a (small Ollama model) | 20Gi default (often skipped locally) | 64-256Mi default | `deploy/clusters/local/values/{inference-gateway,vllm,ollama}.yaml` |
| Small customer (CPU/light GPU) | min 2 / max 20, threshold **25**/s | vLLM 2 replicas KEDA min, GPUs per the model class | `maxModelLen` per need | 100Gi | default | `deploy/clusters/customer/values/inference-gateway.yaml`, `qdrant-vector-store.yaml` |
| GPU coding-agent (Qwen3-Coder-Next) | min 2 / max 20, threshold 25/s | vLLM 2-8 replicas (KEDA queue, threshold 5), **4 GPUs/replica, TP 4** | 262144 (`maxModelLen`) | 100Gi | redis backend | `deploy/clusters/customer/values/{inference-gateway,vllm-nvidia,qdrant-vector-store}.yaml` |

Notes on the examples:

- **Laptop / local** keeps `vLLM replicaCount: 0` and `accelerator.enabled: false`
  (`deploy/clusters/local/values/vllm.yaml`); inference is served by Ollama on CPU with a small model
  (`qwen2.5:0.5b` in the gateway overlay). Keep vLLM at zero unless the workstation has a supported
  GPU setup exposed to Kubernetes (see the runbook's Mitigation section).
- **Small customer** is the gateway/RAG control plane scaled for real traffic; attach whatever GPU
  runtime the model needs. The gateway already uses `budget.backend: redis` because it runs
  `minReplicaCount: 2`.
- **GPU coding-agent** is the full default profile: 4 GPUs per replica with tensor parallelism 4, a
  262k context window, shared 400Gi RWX weight cache, and queue-based vLLM autoscaling 2-8. This is the
  configuration the GPU-capacity runbook's third sizing row describes.

## After sizing: validate

- Run `make loadtest-local` against the customer profile and watch
  `inference_gateway_request_duration_seconds` (p50/p99 by route) and
  `inference_gateway_requests_total{status="200"}` versus 503 load-shed counts.
- Watch `vllm:num_requests_waiting` -- a persistent backlog above `keda.threshold` means add vLLM
  replicas or GPUs; a queue that is always empty means you over-provisioned.
- Confirm gateway replicas track demand (the KEDA request-rate metric) and that
  `maxConcurrentRequests` is not silently shedding at peak.
- Re-derive the worksheet with measured `T_tok` and observed concurrency, then adjust the floors
  (`minReplicaCount`, `accelerator.count`) by hand and let KEDA handle the headroom.

## Related

- [GPU capacity runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/gpu-capacity.md) -- scheduling failures, per-model VRAM/concurrency
  estimates, multi-node serving.
- [SLO and error budget](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/slo-error-budget.md) -- the latency/availability targets your
  sizing must satisfy.
- [Quota and chargeback](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/quota-chargeback.md) -- how the per-sandbox budgets sized in
  Worksheet 4 map to tenants.
