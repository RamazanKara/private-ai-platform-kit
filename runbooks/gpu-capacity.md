# Incident Runbook: GPU Capacity

## Symptoms

vLLM pods remain Pending, GPU utilization is absent after enabling vLLM, or `kubectl describe pod -n vllm` reports that `nvidia.com/gpu` or `amd.com/gpu` is unavailable.

## Inspect

    kubectl get pods -n vllm
    kubectl describe pod -n vllm -l app.kubernetes.io/name=vllm
    kubectl get nodes -L platform.ai/node-pool,platform.ai/gpu-vendor
    kubectl describe node <gpu-node-name>
    kubectl get daemonset -A | grep -i nvidia
    kubectl get daemonset -A | grep -i amd
    kubectl get nodes -o custom-columns=NAME:.metadata.name,NVIDIA:.status.capacity.nvidia\\.com/gpu,AMD:.status.capacity.amd\\.com/gpu

## Likely Causes

The customer-owned cluster does not expose GPU resources, the NVIDIA or AMD device plugin is missing or unhealthy, GPU nodes do not have the expected `platform.ai/node-pool=gpu` and `platform.ai/gpu-vendor=<nvidia|amd>` labels, the vLLM tolerations do not match the customer's GPU node taints, or the selected model requests more GPUs than the cluster can schedule.

## Mitigation

Install or repair the NVIDIA or AMD device plugin according to the customer's Kubernetes platform standard. NVIDIA profiles request `nvidia.com/gpu`; AMD profiles request `amd.com/gpu`. The default Qwen3 Coder Next profile requests four GPUs per replica; reduce `accelerator.count`, `--tensor-parallel-size`, `model.maxModelLen`, or the model itself when targeting smaller clusters. Label GPU nodes with `platform.ai/node-pool=gpu` and `platform.ai/gpu-vendor=nvidia` or `platform.ai/gpu-vendor=amd`, or change the vLLM values to match the customer's existing labels. If GPU nodes are tainted, add matching tolerations to the vLLM chart values. For local `kind`, keep vLLM replicas at `0` unless the workstation has a supported GPU setup exposed to Kubernetes.

Use these profiles as starting points:

    deploy/clusters/customer/values/vllm-nvidia.yaml
    deploy/clusters/customer/values/vllm-amd.yaml

For AMD ROCm vLLM, use an ROCm-compatible vLLM image and verify that the worker nodes have ROCm-capable AMD GPUs, drivers, and the AMD Kubernetes device plugin.

## Sizing Estimates

These are planning estimates to size a starting point, not guarantees. Always validate the chosen GPU class, parallelism, and replica count with a real load test (`make loadtest-local` against the customer profile) before committing to capacity. VRAM figures assume a quantized or BF16/FP16 serving build; actual usage depends on weight precision, KV-cache size at your context length and batch, and the serving runtime. Concurrency is rough simultaneous in-flight requests at a usable interactive latency, not a throughput ceiling.

| Model (class) | Approx weight VRAM | Recommended GPU class | Rough concurrency |
| --- | --- | --- | --- |
| 7-8B dense (e.g. an 8B chat model) | ~16-20 GB (FP16) / ~6-8 GB (4-bit) | 1x 24 GB (L4 / RTX 4090 / A10) | ~8-16 requests per replica |
| 30-35B MoE, ~3B active (e.g. `Qwen/Qwen3.6-35B-A3B`) | ~70-80 GB (FP16) / ~20-24 GB (4-bit) | 1-2x 80 GB (A100 / H100), or 2x 48 GB | ~4-8 requests per replica |
| `Qwen/Qwen3-Coder-Next` coding profile (long context) | ~140-180 GB across GPUs at FP16 | 4x 48-80 GB with tensor parallelism (default profile requests 4 GPUs per replica) | ~2-6 concurrent coding sessions per replica |

Notes:

- KV-cache, not weights, usually dominates at long context. The Qwen3 Coder Next profile defaults to a large context window, so size memory headroom for the context length you actually enable (`model.maxModelLen`) rather than the model maximum.
- 4-bit quantization roughly halves or quarters weight VRAM but can reduce quality; validate evals before relying on it for coding agents.
- To fit smaller clusters, reduce `accelerator.count`, `--tensor-parallel-size`, `model.maxModelLen`, replicas, or the model itself, as described under Mitigation.

## Multi-Node Serving (Models Larger Than One Node)

Single-node tensor parallelism (`--tensor-parallel-size`, via the chart's `extraArgs`) scales
across the GPUs of one node. For a model too large for any single node, combine
tensor-parallel within a node with **pipeline parallelism across nodes**
(`--pipeline-parallel-size`, also via `extraArgs`) and a gang-scheduled leader/worker topology.

This needs a multi-pod primitive the single Deployment chart does not provide — the
[LeaderWorkerSet](https://github.com/kubernetes-sigs/lws) operator (or Ray). Install the LWS
controller, then run vLLM as a `LeaderWorkerSet` whose leader and workers form one Ray cluster:

```yaml
apiVersion: leaderworkerset.x-k8s.io/v1
kind: LeaderWorkerSet
metadata:
  name: vllm-multinode
  namespace: vllm
spec:
  replicas: 1
  leaderWorkerTemplate:
    size: 2                 # 1 leader + 1 worker node (set to the pipeline-parallel size)
    leaderTemplate:
      spec:
        containers:
          - name: vllm
            image: vllm/vllm-openai
            args: ["--model", "Qwen/Qwen3-Coder-Next",
                   "--tensor-parallel-size", "8", "--pipeline-parallel-size", "2"]
            resources: { limits: { nvidia.com/gpu: "8" } }
    workerTemplate:
      spec:
        containers:
          - name: vllm
            image: vllm/vllm-openai
            resources: { limits: { nvidia.com/gpu: "8" } }
```

Set `tensor-parallel-size` to the GPUs per node and `pipeline-parallel-size` to the node
count (`size`); point the gateway's `VLLM_BASE_URL` at the leader Service. This is an
operator-supplied topology — validate the per-node GPU fit and the Ray cluster formation
before promotion.

## Choosing a Quantization Method

Quantization is the single biggest lever for fitting a modern coding/MoE model onto fewer GPUs.
The vLLM chart exposes `server.quantization`, `server.kvCacheDtype`, and `server.gpuMemoryUtilization`
as first-class values, and two ready profiles ship under `deploy/clusters/customer/values/`:

| GPU class | Method | Profile | Notes |
| --- | --- | --- | --- |
| Hopper/Ada (H100, H200, L40S) | FP8 weights + FP8 KV cache | `vllm-nvidia-fp8.yaml` | ~2x memory saving; native FP8, smallest quality cost. Cuts the 4-GPU floor to ~2. |
| Ampere (A100, A10) | AWQ 4-bit weights | `vllm-nvidia-awq.yaml` | Point `model.name` at a pre-quantized `…-AWQ` checkpoint. No FP8 KV cache on Ampere. |
| Any | GPTQ 4-bit weights | set `server.quantization: gptq` | Alternative 4-bit path when an AWQ checkpoint is unavailable. |

Always keep `--tensor-parallel-size` (in `extraArgs`) equal to `accelerator.count`, and re-validate
answer quality with `make eval` after changing quantization — it trades a small accuracy cost for
GPU cost, and the acceptable trade is workload-specific.

## Speculative Decoding

`server.speculative.enabled` + `server.speculative.config` render `--speculative-config` to cut
latency by drafting tokens with a small model (or n-gram) and verifying them in one pass. It only
helps when the draft acceptance rate is high, so enable it per model and confirm the tokens/sec and
p95 latency improve under `make loadtest` before promoting; a low acceptance rate can be net-negative.

## Sub-GPU Sharing (MIG / Time-Slicing)

When a runtime does not need a whole GPU (small models, dev/eval namespaces), share one physical GPU:

- **MIG (A100/H100)**: partition the GPU into isolated instances via the NVIDIA GPU Operator's MIG
  config, then request the MIG resource name (e.g. `nvidia.com/mig-1g.10gb`) in
  `accelerator.resourceName` with `accelerator.count: 1`. Hardware-isolated; best for multi-tenant.
- **Time-slicing**: enable time-slicing in the device plugin config to oversubscribe a GPU across
  pods. No memory isolation — use only for trusted dev/eval workloads, never for tenant isolation.

Both are cluster-level device-plugin/operator settings the operator owns; the chart only needs the
resulting `accelerator.resourceName`/`count`. Size with the same KV-cache math in Sizing Estimates.

## Evidence

Capture pod events, node labels, node allocatable GPU resources, NVIDIA or AMD device plugin logs, and the vLLM values used for scheduling.
