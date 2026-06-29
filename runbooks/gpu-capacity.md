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

    clusters/customer/values/vllm-nvidia.yaml
    clusters/customer/values/vllm-amd.yaml

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

## Evidence

Capture pod events, node labels, node allocatable GPU resources, NVIDIA or AMD device plugin logs, and the vLLM values used for scheduling.
