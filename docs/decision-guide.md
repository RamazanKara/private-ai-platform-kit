# Decision Guide

Private AI Platform Kit is for teams that want a private AI operating model on Kubernetes, not only a model runtime.

## Best Fit

Use this project when you need:

- Local-first validation that can graduate to customer-owned Kubernetes.
- OpenAI-compatible private chat-completion traffic through a controlled gateway.
- Ollama for local labs and vLLM profiles for NVIDIA or AMD GPU clusters.
- RAG, coding-agent workspaces, sandbox tracing, budgets, egress approvals, model governance, SLOs, restore drills, and release evidence in one repo.
- Provider-neutral GitOps and Helm surfaces instead of cloud-specific Terraform.

## Poor Fit

Choose another path when you need:

- A hosted AI gateway with managed identity, billing, and support.
- A single-machine personal Ollama setup.
- A general-purpose distributed training or batch inference platform.
- Cloud infrastructure provisioning as the primary deliverable.
- Production support without owning Kubernetes operations.

## Comparison

| Option | Strength | Tradeoff |
| --- | --- | --- |
| Plain Ollama or vLLM chart | Fastest way to serve a model. | Leaves auth, budgets, evidence, tenant isolation, RAG, and operations to you. |
| Private AI Platform Kit | End-to-end operating model for private LLM and coding-agent workloads. | Requires Kubernetes, Helm, and platform ownership. |
| KubeAI-style platform | Higher-level model serving abstraction. | May not include the same governance, evidence, and customer-handoff controls. |
| Ray Serve | Strong distributed serving and Python-native scaling. | Less focused on locked-down Kubernetes tenant operations and evidence packs. |
| Hosted gateway | Low operational burden. | Data, control plane, and model-routing policy usually leave the customer-owned boundary. |

## Architecture Support

Container images are published multi-arch (`linux/amd64` and `linux/arm64`), so the kit runs unchanged on Apple Silicon developer laptops and on arm64 nodes (AWS Graviton, Azure/GCP Ampere) as well as x86_64 clusters.

## Maturity Position

The project is usable as a reference implementation and local/customer lab. Treat production use as a controlled handoff: replace sample evidence with current evidence, wire customer identity and secrets, size runtime capacity, validate backups, and run strict release gates.
