# Cost Model And TCO Reference

This document is a planning reference for the total cost of ownership of a Private AI Platform Kit
deployment, plus a consolidated set of capacity and scaling reference numbers pulled from the
charts, governance plans, and the GPU capacity runbook. Use it to build an internal budget and a
build-versus-buy case before adoption.

!!! warning "These are planning estimates, not quotes"
    Every dollar figure and node count below is a planning estimate to anchor a budget
    conversation, not a quote, a benchmark, or a guarantee. The kit ships no pricing data and makes
    no infrastructure purchases on your behalf. GPU, CPU, storage, and egress prices vary by
    provider, region, commitment term, and negotiated discount; operator effort varies by team
    maturity. Validate the chosen GPU class, parallelism, and replica count with a real load test
    (`make loadtest-local` against the customer profile) before committing capital, and confirm
    every unit price against a current quote from your own provider or hardware vendor. The kit
    provisions no cloud infrastructure, operates no cluster, and hosts no model (see the Support
    Boundaries section of the [README](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/README.md)).

## What Drives Cost

A Private AI Platform Kit deployment has four cost buckets. Roughly in descending order of spend
for a GPU deployment:

1. **GPU compute** for the model runtime (vLLM). Dominant for any real inference workload, and the
   single number worth modeling carefully. The kit runs CPU-only on `kind` with Ollama for labs, so
   GPU cost is zero until you enable a vLLM profile.
2. **CPU compute** for the platform services: the inference gateway, RAG service, Qdrant, budget
   Redis, agent workspaces, and the observability and GitOps control plane (Argo CD, Kyverno, KEDA,
   external-secrets, kube-prometheus-stack, Loki, Promtail, pushgateway, OpenCost).
3. **Storage** for model weights, vector data, agent workspaces, logs, and backups.
4. **Operator effort** to own the cluster, secrets, identity, backups, and incident response. The
   kit explicitly delegates these to the operator and does not replace them.

The kit's own cost controls (token budgets, the `/v1/usage` estimated cost, OpenCost cost-center
labels) attribute and cap the first three buckets but do not change the underlying provider price.

## TCO Breakdown By Reference Size

The three reference sizes below mirror the kit's own profiles: the local lab on `kind`, a
single-tenant GPU customer cluster, and a multi-tenant coding-agent platform. Quantities are
grounded in the shipped charts and `platform/governance/quota-plans.yaml`; unit prices are
placeholders you must replace.

### Size 1: Local workstation lab (CPU only, Ollama)

This is what `make quickstart` and `make local-up` run. It is the `local-lab` plan in
`platform/governance/quota-plans.yaml` and uses the local cluster values under
`deploy/clusters/local`.

| Component | Quantity (grounded) | Cost driver |
| --- | --- | --- |
| Compute | One developer workstation or a small CPU node | Existing hardware; effectively $0 incremental |
| Model runtime | Ollama, `qwen2.5:0.5b` default smoke model | CPU only, no GPU |
| Ollama weights PVC | `20Gi` (`deploy/charts/ollama/values.yaml`) | Local-path storage |
| Qdrant PVC | `20Gi` (`deploy/charts/qdrant-vector-store/values.yaml`) | Local-path storage |
| Agent workspace PVC | `10Gi` per workspace (`deploy/charts/agent-workspace/values.yaml`) | Local-path storage |
| Gateway sandbox budget | 250 req / 500k prompt chars / 150k est tokens per day (`local-lab` plan) | Caps lab spend |
| Operator effort | One engineer, part-time | Setup and validation only |

Planning estimate: effectively zero incremental cash cost on existing hardware. The dominant cost is
the engineer's time to run the validation flow in [getting-started.md](getting-started.md). The
reference serving benchmark in [benchmarks-and-evals.md](benchmarks-and-evals.md) was produced on a
single consumer CPU (AMD Ryzen 7 5800X3D) at ~55 tokens/s for the 0.5B model, which is the realistic
ceiling for this tier.

### Size 2: Single-tenant GPU customer cluster (vLLM)

This is the `customer-shared-runtime` plan: one inference namespace, a GPU-backed vLLM runtime, the
gateway, RAG, and the full control plane. Use the NVIDIA or AMD profile under
`deploy/clusters/customer/values`.

| Component | Quantity (grounded) | Cost driver |
| --- | --- | --- |
| GPU runtime | vLLM, `replicaCount: 2`, `accelerator.count: 4` GPUs per replica (`vllm-nvidia.yaml`) | GPU node amortization (largest line) |
| GPU autoscaling | KEDA `minReplicaCount: 2`, `maxReplicaCount: 8` on queue depth (`vllm-nvidia.yaml`) | Peak GPU spend scales 2x-4x at burst |
| Model weight cache | `400Gi` ReadWriteMany shared PVC (`vllm-nvidia.yaml`) | High-throughput shared/file storage |
| Platform CPU | Gateway + RAG + Qdrant + Redis + control plane; `requestsCpu: 16`, `requestsMemory: 64Gi` quota (`customer-shared-runtime`) | CPU node-hours |
| Qdrant PVC | `20Gi` default, size to corpus | Block storage |
| Observability storage | Prometheus TSDB + Loki log volume (retention-driven) | Grows with traffic and retention window |
| Backups | Velero schedule + restore-drill (`deploy/backup`) | Object storage + drill compute |
| Operator effort | Cluster, secrets, identity, backups, on-call | Recurring staff cost |

GPU node amortization is the number to model first. The default coding profile requests 4 GPUs per
replica at `minReplicaCount: 2`, so the steady-state floor is 8 GPUs and the KEDA ceiling
(`maxReplicaCount: 8`) is 32 GPUs at full burst. Amortize the GPU node cost (purchase price over the
hardware's useful life, or the cloud GPU-hour rate times utilization) across the tokens you actually
serve. Pin both `keda.maxReplicaCount` and the gateway sandbox budget to the spend ceiling you can
afford so a traffic spike cannot silently scale to the GPU maximum.

The GPU class itself depends on the model. From `runbooks/gpu-capacity.md`:

| Model class | Approx weight VRAM | Recommended GPU class | Rough concurrency |
| --- | --- | --- | --- |
| 7-8B dense | ~16-20 GB FP16 / ~6-8 GB 4-bit | 1x 24 GB (L4 / RTX 4090 / A10) | ~8-16 req per replica |
| 30-35B MoE (~3B active) | ~70-80 GB FP16 / ~20-24 GB 4-bit | 1-2x 80 GB (A100/H100) or 2x 48 GB | ~4-8 req per replica |
| `Qwen/Qwen3-Coder-Next` (long context) | ~140-180 GB across GPUs at FP16 | 4x 48-80 GB with tensor parallelism | ~2-6 coding sessions per replica |

KV-cache, not weights, usually dominates at long context: the default coding profile sets
`model.maxModelLen: "262144"`, so size memory headroom for the context length you actually enable.
These concurrency figures are rough simultaneous in-flight requests at a usable interactive latency,
not a throughput ceiling — re-derive them per model with a load test.

### Size 3: Multi-tenant coding-agent platform

This adds the `coding-agents-lab` plan on top of Size 2: many isolated agent workspaces sharing the
GPU runtime, each with its own PVC, quota, and sandbox budget.

| Component | Quantity (grounded) | Cost driver |
| --- | --- | --- |
| Shared GPU runtime | Same vLLM tier as Size 2, sized to aggregate concurrency | GPU amortization, shared across tenants |
| Agent workspaces | `maxConcurrentAgents: 20`, `pvcSize: 50Gi` per tenant (`coding-agents-lab`) | Per-tenant block storage (up to 1 TB at 20 x 50Gi) |
| Tenant CPU quota | `requestsCpu: 8`, `requestsMemory: 16Gi`, `pods: 40` per tenant | CPU node-hours per tenant |
| Per-tenant budget | 5000 req / 25M prompt chars / 7.5M est tokens per day (`coding-agents-lab`) | Caps each tenant's GPU draw |
| Chargeback | OpenCost cost-center labels per tenant (see below) | Attribution, not new spend |

The economic advantage here is sharing one GPU tier across tenants while still capping and
attributing each tenant's draw. Per-tenant cost is the tenant's share of the shared GPU runtime
(proportional to its token usage) plus its dedicated workspace storage and CPU quota. The chargeback
labels make that split auditable rather than estimated.

## How The Kit's Cost Controls Map To Spend

The kit does not buy capacity, but it does cap, estimate, and attribute it. Three mechanisms matter
for cost.

### Per-sandbox token budgets cap the GPU draw

The gateway enforces three ceilings per `X-Sandbox-ID` — accepted request count, cumulative prompt
characters, and cumulative estimated tokens — over a rolling window (`windowSeconds`, default
`86400`). Because GPU spend is driven by tokens served, the estimated-token budget is the direct
spend cap: a sandbox that hits `estimatedTokenLimit` is rejected with HTTP 400 and stops drawing GPU
time. Estimated tokens are computed as
`ceil(prompt_chars / estimatedCharsPerToken) + requested max_tokens`, so the cap is conservative
(it counts requested completion tokens up front). Budgets default to a Redis backend so they hold
across gateway replicas. See [budget-controls.md](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/budget-controls.md) for configuration
and the per-plan ceilings in `platform/governance/quota-plans.yaml`.

To translate a token budget into a dollar ceiling, multiply the estimated-token limit by your
per-1k-token cost (derived from GPU node-hours divided by tokens served at your measured
throughput). The kit exposes exactly that conversion through `/v1/usage`.

### `/v1/usage` turns token usage into an estimated cost

The gateway's `/v1/usage` endpoint (`src/inference-gateway/app/main.py`) returns per-sandbox usage
plus an estimated monetary cost. The cost formula is:

```
estimated_cost = round((estimated_tokens / 1000.0) * usd_per_1k_tokens, 6)
```

The rate is operator-supplied via `USD_PER_1K_TOKENS` (env) / `usd_per_1k_tokens` (settings) and
**defaults to `0.0`**, which leaves the cost model off (estimated cost is always `0`) until you set
a rate. The currency string defaults to `USD` (`COST_CURRENCY` / `cost_currency`). The response
shape is:

```json
{
  "sandbox_id": "local-lab",
  "usage": { "requests": 3, "prompt_chars": 1200, "estimated_tokens": 800 },
  "limits": { "requests": 250, "prompt_chars": 500000, "estimated_tokens": 150000 },
  "estimated_cost": 0.0016,
  "currency": "USD",
  "usd_per_1k_tokens": 2.0
}
```

This is a self-reported planning estimate from the gateway's own token accounting, not a billing
system. Set `usd_per_1k_tokens` to your modeled blended cost per 1k tokens (GPU amortization plus a
share of platform overhead) so the endpoint reflects your real internal cost. It is the data layer
an admin or usage console renders; it does not meter your provider or issue invoices.

### OpenCost cost-center labels attribute infrastructure spend

For the infrastructure side (node-hours, PVCs, not tokens), the kit ships OpenCost as the
`cost-controls` Argo CD application (`deploy/clusters/local/apps.yaml`, OpenCost Helm chart
`1.41.0`, cluster id `private-ai-platform-kit-local`). Attribution depends on stable labels, and the
kit makes those labels mandatory rather than optional: the `ai-platform-required-labels` Kyverno
`ClusterPolicy` in `deploy/policies/kyverno/policies.yaml` runs with `validationFailureAction:
Enforce` and rejects any Pod (outside infra namespaces) that is missing the standard ownership and
cost labels, including `platform.ai/cost-center`, `platform.ai/owner`, `platform.ai/environment`,
and `platform.ai/sandbox-id`.

Because the policy is enforcing, every workload that runs carries a cost center, so OpenCost,
Prometheus, logs, and evidence packs all attribute spend to the same keys. The chargeback contract
in `platform/governance/quota-plans.yaml` (allocation unit `sandbox-day`, reporting currency USD)
uses these same required labels, so showback or chargeback per tenant is auditable, not estimated.
Keep the labels stable across upgrades so cost history stays comparable
([quota-chargeback.md](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/quota-chargeback.md)).

### Storage and observability cost notes

- **Model weights** are the largest storage line on a GPU cluster. The customer vLLM profile uses a
  `400Gi` ReadWriteMany cache so 100+ GB weights are not re-downloaded on every pod restart or KEDA
  scale-up. RWX/file storage is typically priced higher than block; if only ReadWriteOnce is
  available the profile notes you should pin `replicaCount: 1` or pre-bake weights into the image.
- **Vector data** scales with corpus size and embedding dimensionality; the Qdrant PVC default is
  `20Gi`. Size it to your corpus and re-measure after ingestion.
- **Agent workspaces** are `10Gi` per workspace by default (`agent-workspace` chart), `50Gi` per
  tenant in the `coding-agents-lab` plan, multiplied by `maxConcurrentAgents`.
- **Loki + Prometheus** storage grows with traffic and retention. Loki ships in `SingleBinary` mode
  with `replication_factor: 1` (`deploy/observability/applications.yaml`); set retention to bound
  log storage cost. The redacted audit JSON flows through Promtail into Loki, so audit retention is
  a deliberate cost/compliance tradeoff.
- **Backups** add object storage plus restore-drill compute (`deploy/backup`).

## Build Versus Buy

A like-for-like comparison against a hosted API has to account for what each side actually includes.

| Dimension | This kit (build, self-host) | Hosted AI API / gateway (buy) |
| --- | --- | --- |
| Pricing model | Capacity you provision (GPU/CPU node-hours, storage) — largely fixed, plus operator effort | Per-token, usage-metered — fully variable |
| Marginal cost of a token | Near zero once the GPU tier is provisioned (you pay for the node, not the token) | Charged per token, every token, forever |
| Data boundary | Data, control plane, and model-routing policy stay inside the customer boundary | Data and routing policy usually leave the customer boundary |
| Identity / billing / support | You own them (delegated by the kit) | Managed by the provider |
| Idle cost | You pay for provisioned capacity even when idle (mitigate with KEDA scale-down) | Zero when idle |
| Best when | Steady, predictable, privacy-sensitive volume; data must not leave | Spiky or low volume; fast start; no platform team |

The crossover is utilization. A self-hosted GPU tier has a high fixed cost and a near-zero marginal
cost per token, so it wins when utilization is high and steady — the provisioned GPUs are kept busy.
A hosted API has zero fixed cost and a fixed marginal cost per token, so it wins at low or spiky
volume where dedicated GPUs would sit idle. To find your crossover, divide your modeled monthly
fixed cost (GPU amortization plus platform overhead) by the hosted per-token price to get the
break-even token volume; above it, build; below it, buy. The kit's `decision-guide.md` lists
non-cost reasons (data residency, governance, tenant isolation) that can override a pure-cost
verdict, and notes that a hosted gateway is the right call when low operational burden matters more
than keeping the boundary.

The kit lowers the build-side floor in two ways worth crediting in the model: KEDA scale-down on
the GPU runtime (`minReplicaCount`/`maxReplicaCount` on queue depth) reduces idle GPU spend, and the
shared runtime in the multi-tenant size spreads one GPU tier's fixed cost across many tenants.

## How To Build Your Own Estimate

1. Pick the reference size that matches your deployment and confirm the grounded quantities against
   the cited charts and `platform/governance/quota-plans.yaml`.
2. Choose a GPU class from the `runbooks/gpu-capacity.md` table for your model, then validate
   real concurrency and throughput with a load test (`make loadtest-local`, then a live load test
   against the customer profile) — do not trust the rough concurrency figures for capital planning.
3. Get current unit prices (GPU node-hour or purchase price, CPU node-hour, RWX/block/object storage
   per GB-month, egress per GB) from your own provider or hardware quote.
4. Derive a blended cost per 1k tokens from GPU amortization at your measured throughput, set
   `USD_PER_1K_TOKENS` to it, and read estimated cost back from `/v1/usage`.
5. Set per-sandbox token budgets and `keda.maxReplicaCount` to the spend ceiling you can afford.
6. Compare the build total against your hosted per-token quote at your real projected volume to find
   the crossover.

For capacity sizing detail see [GPU capacity runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/gpu-capacity.md); for the
budget, quota, and chargeback controls see [budget-controls.md](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/budget-controls.md) and
[quota-chargeback.md](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/quota-chargeback.md).
