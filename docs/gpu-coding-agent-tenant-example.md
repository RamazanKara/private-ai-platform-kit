# GPU Coding-Agent Tenant Example

This walkthrough onboards a coding-agent tenant for a GPU-backed team that calls a vLLM runtime and
needs approved external egress (Git, artifact, and model mirrors). It renders from the reviewed spec
[tenants/onboarding/gpu-coding-agents.yaml](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/tenants/onboarding/gpu-coding-agents.yaml) and pairs
with the [GPU capacity](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/gpu-capacity.md) and [Agent workspaces](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/agent-workspaces.md) runbooks.

## What This Profile Provides

| Control | Value | Effect |
| --- | --- | --- |
| `compliance.profile` | `gpu-agent` | Marks the namespace as a GPU agent workload. |
| `compliance.dataClassification` | `internal` | Standard internal classification. |
| `compliance.externalEgressAllowed` | `true` | Approved external CIDRs are rendered into the egress allowlist. |
| `network.allowedEgressCidrs` | two reviewed CIDRs | Git/artifact mirror (`203.0.113.0/24:443`) and model cache (`198.51.100.0/24:443`). |
| `quotas` | 16-32 CPU, 64-128Gi | Sized for GPU-adjacent build and agent workloads. |
| `agentWorkspace.pvcSize` | `100Gi` | Larger workspace for model and build artifacts. |
| `agentWorkspace.rbac.allowJobManagement` | `true` | Agents may create and manage Jobs (e.g. eval/build jobs). |

Unlike the [regulated offline profile](regulated-offline-tenant-example.md), this one renders a
NetworkPolicy that permits the two named external CIDRs on port 443 in addition to DNS and the
in-cluster gateway and RAG service.

## Prerequisite: GPU Runtime

This tenant assumes a vLLM runtime is already serving on GPU nodes. Label GPU nodes and deploy the
vLLM chart per the [Customer cluster README](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/clusters/customer/README.md):

```bash
kubectl label node <gpu-node> platform.ai/node-pool=gpu platform.ai/gpu-vendor=nvidia
# vLLM customer profile: deploy/clusters/customer/values/vllm-nvidia.yaml (or vllm-amd.yaml)
```

See [GPU capacity](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/gpu-capacity.md) for sizing tensor parallelism, context length, and
GPU requests.

## Render The Tenant Artifacts

```bash
make tenant-onboard-gpu TENANT_OUTPUT=tenants/generated
```

Equivalent to:

```bash
python3 scripts/tenant-onboard.py \
  --spec tenants/onboarding/gpu-coding-agents.yaml \
  --output-dir tenants/generated
```

Review `tenants/generated/` and confirm:

- the egress NetworkPolicy lists **only** the two approved CIDRs (`203.0.113.0/24`, `198.51.100.0/24`) on port 443, plus DNS, gateway, and RAG;
- the workspace PVC requests `100Gi` and the Role allows job management;
- quotas and the limit range match the GPU team's footprint.

## Apply And Verify

```bash
kubectl apply -f tenants/generated/
make tenant-smoke
```

Verify egress is allowlisted, not open:

```bash
# Approved mirror reachable (replace with a real host in the approved CIDR):
kubectl -n ai-gpu-coding-agents run ok --rm -it --image=curlimages/curl --restart=Never -- \
  curl -m 5 -sS -o /dev/null -w '%{http_code}\n' https://<host-in-203.0.113.0/24>

# Arbitrary external host blocked:
kubectl -n ai-gpu-coding-agents run blocked --rm -it --image=curlimages/curl --restart=Never -- \
  curl -m 5 https://example.com   # expected: timeout / blocked
```

## Customizing

- Replace the example CIDRs (`203.0.113.0/24`, `198.51.100.0/24`) with the customer's real,
  reviewed mirror ranges before applying; keep the `catalogRef`/`description` so each allow is
  auditable.
- Tune `quotas`, `limitRange`, and `agentWorkspace.pvcSize` to the GPU footprint.
- Set `allowJobManagement: false` if the team should not create Jobs.
