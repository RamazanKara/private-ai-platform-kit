# Customer-Owned Kubernetes Deployment

This overlay is for customers who already operate Kubernetes. It does not create cloud infrastructure and does not assume a specific managed Kubernetes service.

## What Customers Provide

- Kubernetes cluster with a default StorageClass.
- Ingress or an approved port-forwarding path.
- Argo CD installed in the target cluster.
- Secret backend compatible with External Secrets Operator.
- Optional GPU nodes that expose `nvidia.com/gpu` or `amd.com/gpu`.
- Customer-owned Git repository, fork, or mirror for this repo.
- Existing logging, metrics, alerting, backup, and incident-management integrations.

## 1. Configure The GitOps Overlay

Set every Argo CD `repoURL` to the customer fork or mirror, pin the revision to the branch or tag you want to deploy, and choose the active vLLM GPU profile:

```bash
make customer-overlay \
  CUSTOMER_REPO_URL=https://github.com/<customer>/<repo>.git \
  CUSTOMER_REVISION=v0.4.2 \
  CUSTOMER_GPU_PROFILE=nvidia
```

Use `CUSTOMER_GPU_PROFILE=amd` for AMD ROCm clusters. Use `CUSTOMER_GPU_PROFILE=default` to keep `clusters/customer/values/vllm.yaml`.

Validate the overlay without changing files:

```bash
make customer-overlay-check
```

The configurator updates:

- `gitops/argocd/root-app-customer.yaml`
- all child Applications in `clusters/customer/apps.yaml`
- the `runtime-vllm` value file selection

## 2. Prepare Secrets

Gateway and RAG business endpoints read SHA-256 API-key hashes from External Secrets. Do not store plaintext API keys in Helm values.

Create a hash for each customer API key:

```bash
printf '%s' "$PLATFORM_API_KEY" | sha256sum | awk '{print $1}'
```

Publish the comma-separated hashes as `api-key-sha256s` in the customer secret backend. If the Kubernetes provider example in `external-secrets.yaml` is used for a lab, the backing secret must live in the `platform-secrets` namespace under the `ai-platform-customer-secrets` key.

vLLM model pulls use `hf-token` from the same External Secrets path when the selected model requires Hugging Face access.

## 3. Confirm GPU Scheduling

NVIDIA clusters should expose:

```text
nvidia.com/gpu
```

AMD clusters should expose:

```text
amd.com/gpu
```

Label GPU nodes:

```bash
kubectl label node <node> platform.ai/node-pool=gpu platform.ai/gpu-vendor=<nvidia|amd>
```

If GPU nodes are tainted, keep the matching tolerations in `clusters/customer/values/vllm-nvidia.yaml` or `clusters/customer/values/vllm-amd.yaml`.

The default customer model profile targets `Qwen/Qwen3-Coder-Next` and requests four GPUs per vLLM replica. Reduce `accelerator.count`, `model.maxModelLen`, tensor parallelism, replica counts, or the model itself for smaller clusters.

## 4. Review Customer Values

Review these before applying the overlay:

| File | Decision |
| --- | --- |
| `values/inference-gateway.yaml` | Runtime backend, allowed models, API-key secret, budgets, KEDA limits |
| `values/vllm-nvidia.yaml` or `values/vllm-amd.yaml` | Model, image, GPU count, autoscaling, node selectors, tolerations |
| `values/rag-service.yaml` | Qdrant URL, collection name, vector dimensions, API-key secret |
| `values/qdrant-vector-store.yaml` | Storage class, storage size, resources, backup expectations |
| `values/agent-workspace.yaml` | Tenant labels, PVC size, quotas, approved external egress |
| `external-secrets.yaml` | Secret store provider and remote secret keys |
| `gpu-scheduling.yaml` | GPU resource-name and node-label contract |

For regulated or offline teams, start from `tenants/onboarding/regulated-offline-coding-agents.yaml`. It renders confidential labels, no external CIDR egress, and no default job-management RBAC.

## 5. Apply

After committing the configured overlay to the customer repo, bootstrap or sync Argo CD:

```bash
ENVIRONMENT=customer make bootstrap-argocd
ENVIRONMENT=customer make sync
```

If Argo CD cannot reach the repository, fix the `repoURL` values with `make customer-overlay` and sync again.

## 6. Smoke Test

Port-forward or use the customer ingress path for the inference gateway, then run:

```bash
GATEWAY_URL=http://127.0.0.1:8080 make eval
GATEWAY_URL=http://127.0.0.1:8080 make loadtest
```

For in-cluster validation after the local lab is synced:

```bash
make evidence LIVE=1
make release-gate-strict
```

## Handoff Checklist

- API-key hashes are sourced from the customer secret backend.
- Gateway and RAG business endpoints require `X-API-Key` or Bearer auth.
- `runtime.allowedModels` contains only approved model IDs.
- Model provenance is replaced with customer model-store digests before production use.
- RAG knowledge and vector collections contain only approved customer content.
- Agent egress uses reviewed entries from `network/egress-catalog.yaml`.
- Restore-drill evidence is generated and retained under the customer policy.
- SLO, quota, retention, egress, model, eval, load, and evidence reports pass strict release gates without falling back to checked-in samples.
