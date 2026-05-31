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

The customer-owned cluster does not expose GPU resources, the NVIDIA or AMD device plugin is missing or unhealthy, GPU nodes do not have the expected `platform.ai/node-pool=gpu` and `platform.ai/gpu-vendor=<nvidia|amd>` labels, or the vLLM tolerations do not match the customer's GPU node taints.

## Mitigation

Install or repair the NVIDIA or AMD device plugin according to the customer's Kubernetes platform standard. NVIDIA profiles request `nvidia.com/gpu`; AMD profiles request `amd.com/gpu`. Label GPU nodes with `platform.ai/node-pool=gpu` and `platform.ai/gpu-vendor=nvidia` or `platform.ai/gpu-vendor=amd`, or change the vLLM values to match the customer's existing labels. If GPU nodes are tainted, add matching tolerations to the vLLM chart values. For local `kind`, keep vLLM replicas at `0` unless the workstation has a supported GPU setup exposed to Kubernetes.

Use these profiles as starting points:

    clusters/customer/values/vllm-nvidia.yaml
    clusters/customer/values/vllm-amd.yaml

For AMD ROCm vLLM, use an ROCm-compatible vLLM image and verify that the worker nodes have ROCm-capable AMD GPUs, drivers, and the AMD Kubernetes device plugin.

## Evidence

Capture pod events, node labels, node allocatable GPU resources, NVIDIA or AMD device plugin logs, and the vLLM values used for scheduling.
