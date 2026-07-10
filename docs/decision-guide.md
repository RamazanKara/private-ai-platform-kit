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

The kit is not the best tool for every job. Several mature open-source projects do parts of this better, and you should reach for them when their focus matches your need.

| Option | Does better than this kit | Tradeoff versus this kit |
| --- | --- | --- |
| Private AI Platform Kit | End-to-end operating model: gateway, RAG, coding-agent tenancy, governance, and release evidence in one repo. | Requires Kubernetes, Helm, and platform ownership; opinionated rather than a drop-in library. |
| Plain Ollama or vLLM chart | Fastest way to serve a single model with the least moving parts. | Leaves auth, budgets, evidence, tenant isolation, RAG, and operations to you. |
| [LiteLLM](https://github.com/BerriAI/litellm) | Far broader provider/model routing, a polished proxy, and richer per-key spend tracking and rate limiting out of the box. | Is a gateway/proxy, not a full Kubernetes operating model: no bundled RAG, tenant isolation manifests, or release-evidence pipeline. |
| [BentoML / OpenLLM](https://github.com/bentoml/OpenLLM) | Smoother model packaging and serving developer experience, with flexible Python service composition and autoscaling. | Centered on serving and packaging, not locked-down multi-tenant cluster operations, egress governance, or customer-handoff evidence. |
| [KServe](https://github.com/kserve/kserve) | More general, standards-based model serving (multi-framework, canary, payload logging) at large scale on Kubernetes. | Heavier and lower-level; you still assemble the gateway policy, RAG, budgets, and evidence story yourself. |
| [KubeAI](https://github.com/substratusai/kubeai) | Simpler, more turnkey Kubernetes model serving with built-in OpenAI-compatible endpoints and autoscaling, including scale-from-zero. | Focuses on serving and scaling, so it does not include the same governance, evidence, tenant-isolation, and customer-handoff controls. |
| [Ray Serve](https://github.com/ray-project/ray) | Strong distributed serving and Python-native scaling for custom multi-model pipelines. | Less focused on locked-down Kubernetes tenant operations and evidence packs. |
| Hosted gateway (managed SaaS) | Low operational burden, managed identity, billing, and support. | Data, control plane, and model-routing policy usually leave the customer-owned boundary. |

## Architecture Support

Container images are published multi-arch (`linux/amd64` and `linux/arm64`), so the kit runs unchanged on Apple Silicon developer laptops and on arm64 nodes (AWS Graviton, Azure/GCP Ampere) as well as x86_64 clusters.

## Agent Workspace Isolation

Coding-agent workspaces run on the hardened kubernetes-sigs/agent-sandbox runtime, which is the
standard and only runtime (ADR 0010). The controller is a platform prerequisite (installed by the
`agent-sandbox-controller` Application or `make agent-sandbox-install`); workspaces get a
controller-managed sandbox pod with no ambient credentials and a short-lived, audience-bound
platform token instead of long-lived secrets. The one environment-dependent choice left is the
kernel-isolation runtime class: set `sandbox.runtimeClassName` (e.g. gVisor) where the cluster
provides one, which is expected at the `high` risk tier (`C-ISOLATE`). NetworkPolicy enforcement requires
a policy-capable CNI; the local lab defaults to pinned Calico, and
`make agent-sandbox-smoke` rejects non-enforcing kindnet instead of recording a vacuous pass. See [agent-sandbox-integration.md](agent-sandbox-integration.md), ADR 0009,
and ADR 0010.

## Maturity Position

The project is usable as a reference implementation and local/customer lab. Treat production use as a controlled handoff: replace sample evidence with current evidence, wire customer identity and secrets, size runtime capacity, move the bundled single-node stateful stores (budget Redis, Qdrant, Loki) to their external/HA path ([production readiness matrix](production-readiness.md)), validate backups, and run strict release gates.
