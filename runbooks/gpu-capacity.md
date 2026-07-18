# GPU capacity

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

## Capacity changes

The checked-in NVIDIA profile requests four GPUs per vLLM replica, sets tensor parallelism to four,
and allows a 262,144-token context. Those values are a render-tested reference profile, not a
hardware recommendation.

Size the actual model artifact on the target GPU. Account for weights, KV cache, activations,
runtime overhead, prompt/completion length, and concurrent requests. Change
`accelerator.count`, tensor parallelism, context length, replica count, and KEDA bounds together.

`make loadtest-local` uses a mock runtime and cannot size a GPU deployment. Point `make loadtest`
at the deployed gateway and collect vLLM queue, latency, token-throughput, and GPU-memory metrics
at the same time. Run the relevant eval suite again after changing the model, precision, or
quantization.

The chart creates a Kubernetes `Deployment`. Its tensor-parallel configuration assumes that all
requested GPUs are available to one pod on one node. The repository does not install or test a
multi-node vLLM operator. A model that cannot fit on one node needs a separately designed and tested
LeaderWorkerSet, Ray, or equivalent deployment.

The FP8 and AWQ files under `deploy/clusters/customer/values/` are configuration examples. The
operator must supply a compatible GPU, runtime image, and model artifact. MIG and time-slicing are
also cluster/device-plugin settings; the chart can request the resulting resource name but does not
configure GPU partitioning.

See [Capacity and sizing](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/docs/capacity-sizing.md)
for the measurement loop.

## Evidence

Capture pod events, node labels, node allocatable GPU resources, NVIDIA or AMD device plugin logs, and the vLLM values used for scheduling.
