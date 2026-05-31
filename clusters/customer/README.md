# Customer-Owned Kubernetes Overlay

This directory contains provider-neutral values and manifests for customers who already operate Kubernetes. It does not provision infrastructure and does not assume any specific managed Kubernetes service.

Customers must provide:

- A Kubernetes cluster with a default StorageClass.
- Ingress or port-forwarding according to their standards.
- Optional GPU nodes that advertise `nvidia.com/gpu` when vLLM is enabled.
- Optional AMD GPU nodes that advertise `amd.com/gpu` when the AMD vLLM profile is enabled.
- A secret backend compatible with External Secrets Operator, or the Kubernetes-provider example in `external-secrets.yaml`.
- A Git repository URL in `gitops/argocd/root-app.yaml`.
- A decision on whether to keep the default `ai-sandbox` namespace or create one sandbox namespace per team.
- A decision on whether to deploy one coding-agent workspace per team, repository, or project boundary.
- A decision on whether regulated or offline teams should use `tenants/onboarding/regulated-offline-coding-agents.yaml`.
- A decision on Qdrant storage class, size, backup policy, embedding vector dimensions, and ingestion ownership when vector RAG is enabled.
- Approved egress CIDRs for coding agents that need Git, package mirrors, artifact stores, issue trackers, or internal documentation.

Every customer sandbox should preserve the request contract documented in `runbooks/traceability-sandbox.md`: send `X-Request-ID`, send `X-Sandbox-ID`, send `X-API-Key` or Bearer auth for business endpoints, propagate `traceparent` when available, and keep raw prompt text out of audit logs.

The customer secret backend should publish `api-key-sha256s` as one or more comma-separated SHA-256 hashes for gateway and RAG access. Do not store plaintext API keys in Helm values.

Coding agents should run through `clusters/customer/values/agent-workspace.yaml` and keep default-deny egress. Extend `networkPolicy.allowedEgressCidrs` only for customer-approved dependencies. The default RAG service values in `clusters/customer/values/rag-service.yaml` should be replaced or extended with approved customer engineering docs, standards, repository maps, and runbooks.

Regulated or offline coding-agent teams should start from `tenants/onboarding/regulated-offline-coding-agents.yaml`. It emits compliance and data-classification labels, renders no external CIDR egress, and disables default job-management RBAC until explicitly reviewed.

Customer RAG values enable the Qdrant vector-store profile by default. Tune `clusters/customer/values/qdrant-vector-store.yaml` for storage and resources, keep the RAG vector dimensions aligned with the embedding strategy, and review `runbooks/vector-rag.md` before loading production knowledge.

For vLLM on NVIDIA, use `clusters/customer/values/vllm-nvidia.yaml`. For vLLM on AMD ROCm, use `clusters/customer/values/vllm-amd.yaml`. Both profiles run multiple replicas, include HPA/PDB controls, and expect GPU nodes to be labeled with `platform.ai/node-pool=gpu` and the matching `platform.ai/gpu-vendor`.
