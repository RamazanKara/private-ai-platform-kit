# Cost model

The repository does not contain current infrastructure prices and cannot produce a deployment quote. This page describes the inputs to collect and the limits of the built-in usage estimate.

## Cost inputs

Build the estimate from the target environment, not from the local lab:

| Area | Inputs |
| --- | --- |
| Model serving | GPU type and count, replicas, measured tokens per second, utilization, model-load time |
| Platform compute | Gateway, RAG, Redis, Qdrant, Argo CD, policy, observability, and workspace CPU/memory |
| Storage | Model cache, Qdrant data, workspace PVCs, metrics/logs, object-store data, and backups |
| Network | Registry/model downloads, user ingress, cross-zone traffic, and backup transfer |
| Operations | Cluster, identity, secret, backup, incident, upgrade, and security-review time |

Use current quotes from the actual provider or hardware vendor. The repository intentionally has no provider price table.

## Checked-in customer defaults

The customer NVIDIA example is large: two vLLM replicas at the KEDA floor, four GPUs per replica, a 262,144-token maximum context, and a 400 GiB shared model cache. These values live in `deploy/clusters/customer/values/vllm-nvidia.yaml` and are test fixtures for a coding-model profile. They are not a recommended starting capacity.

Before pricing the deployment, choose the model and reduce or increase the GPU count, tensor parallelism, context length, replica floor, and storage from measured requirements. The [capacity worksheet](capacity-sizing.md) lists the relevant settings.

## Gateway usage estimate

`GET /v1/usage` reports the gateway's estimated token count for a sandbox. When `USD_PER_1K_TOKENS` is set, it also returns:

```text
estimated_cost = estimated_tokens / 1000 * usd_per_1k_tokens
```

The default rate is `0.0`. The operator supplies the rate.

This is not billing data. The gateway estimates tokens from prompt characters and requested completion limits; it does not read GPU power, node invoices, storage charges, or provider metering. Use the endpoint for a consistent internal estimate only after deriving a rate from measured throughput and the real cost base.

## Budgets and chargeback labels

Sandbox budgets limit requests, prompt characters, and estimated tokens. They bound accepted work according to gateway accounting, but they do not guarantee a currency ceiling: retries, idle capacity, model loading, storage, and platform overhead still cost money.

The local Argo CD profile installs OpenCost and applies owner/cost-center labels. The customer profile does not install OpenCost. Connect the labels to the customer's existing cost system if infrastructure allocation is required.

## Build an estimate

1. Measure the intended model on the intended GPU with the real context and concurrency mix.
2. Set a replica floor that meets baseline traffic and a ceiling the budget can tolerate.
3. Add platform CPU/memory and all persistent data, including backups and retention growth.
4. Add operator time and support obligations.
5. Divide the measured monthly cost by measured successful tokens if an internal per-token rate is useful.
6. Configure that rate in the gateway and compare its estimates with infrastructure invoices over time.

`make loadtest-local` uses a mock runtime and is useful for gateway/report behavior only. To measure a real runtime, point `make loadtest` at the deployed gateway and collect GPU, queue, latency, and error metrics at the same time.

See the [GPU capacity runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/gpu-capacity.md), [budget controls](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/budget-controls.md), and [quota/chargeback runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/quota-chargeback.md).
