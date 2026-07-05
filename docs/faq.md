# FAQ

Short answers for evaluators and operators. Each answer links the authoritative page.

## Evaluating the kit

### Is this production-ready?

It is a reference implementation and customer lab, not a turnkey product. The controls are shaped
like production controls and the local stack is the reference, but a production handoff still requires
current strict evidence, customer identity/secrets integration, capacity sizing, and backup
validation. See [Production readiness](production-readiness.md) and the maturity note on the
[home page](index.md).

### What does the kit NOT do?

It provides Kubernetes manifests, Helm charts, service code, validation tooling, and runbooks. It does
**not** provision cloud infrastructure, operate your cluster, host customer models, or replace your
identity provider, secret manager, logging stack, backup platform, or incident process. The full list
is in [Scope and non-goals](scope-and-non-goals.md).

### Who is this for?

Platform / SRE teams standing up a private-AI stack, operators running it day-2, and security /
compliance reviewers who need traceable controls. Start with the [Decision guide](decision-guide.md)
to check fit; poor-fit signals (you want a managed SaaS, a single-container demo, or a specific
cloud's native AI services) are listed there.

### How much does it cost to run?

That depends on GPU count, node sizing, and storage. The [Cost model](cost-model.md) gives a TCO
breakdown for three reference sizes and maps the kit's own cost controls (per-sandbox token budgets,
the `/v1/usage` estimate, OpenCost cost-center labels) to spend. Use the
[Capacity sizing](capacity-sizing.md) worksheet to size before you price.

## Running it

### Do I need GPUs to try it?

No. The local `kind` profile runs on CPU with Ollama and a small model (`qwen2.5:0.5b`). GPUs are only
needed for the vLLM profiles on customer clusters. See [Quickstart](quickstart.md).

### Local vs customer profile: what changes?

The charts, GitOps layout, policies, runbooks, and evidence checks are the same. The customer profile
assumes Kubernetes already exists and replaces only the platform services you already operate (ingress,
storage classes, secret backends, logging, observability, GPU node pools) plus swaps Ollama for vLLM.
See [Architecture](architecture.md) and the
[Customer cluster README](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/clusters/customer/README.md).

### Ollama or vLLM: when do I use which?

Ollama is the fast, dependency-light local/CPU runtime for laptops and `kind`. vLLM is the
production-style GPU runtime for customer clusters (NVIDIA/AMD), with prefix caching, FP8/AWQ
quantization, and guided/speculative decoding. See
[GPU capacity](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/gpu-capacity.md).

### Can I use the Anthropic SDK / Claude-style agents against it?

Yes. The gateway exposes a native Anthropic `/v1/messages` endpoint. The Anthropic request
and response are translated to and from the internal OpenAI chat shape and run through the
**same** governance path as `/v1/chat/completions` (auth, model allowlist, admission limits,
prompt-secret policy, budget, output guardrail, audit); text is exact and tool blocks are
mapped best-effort. Streaming is not supported on `/v1/messages` in this release (send
`stream: false`, or use `/v1/chat/completions` for OpenAI-shaped streaming). For
Anthropic-shaped features the native endpoint does not yet cover, such as streaming, a
translation sidecar (e.g. a LiteLLM proxy that exposes `/v1/messages` and forwards to the
gateway's `/v1/chat/completions`) remains a supported alternative; a config sketch and a
native-endpoint example are in [Client examples](client-examples.md). The full list of
OpenAI/Anthropic surfaces the gateway does and does not implement is in
[Scope and non-goals](scope-and-non-goals.md).

### How do I upgrade or roll back?

Move the immutable `CUSTOMER_REVISION` tag and let Argo CD sync; roll back by pointing it at the
previous tag. The full order-of-operations and rollback paths are in the
[Upgrade runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/upgrade.md);
pinned/tested component versions are in the [Version matrix](version-matrix.md).

## Security & multi-tenancy

### Is in-cluster traffic encrypted?

Not by default. The data plane is plaintext HTTP, with default-deny NetworkPolicies restricting who
may connect. Encryption in transit is an opt-in, operator-owned CNI/mesh control; the kit ships an
overlay (mesh mTLS, Cilium WireGuard/IPsec, or cert-manager TLS). See the
[Security overview](security-overview.md) and [Threat model](threat-model.md).

### How do I make it multi-tenant safe?

Use per-team sandbox namespaces with quotas and default-deny egress, per-sandbox budgets and rate
limits at the gateway, and enable per-tenant RAG retrieval isolation (`retrieval.tenantIsolation`) so a
tenant only retrieves its own documents. See [Agent workspaces](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/agent-workspaces.md)
and the [Glossary](glossary.md) entries for sandbox-id and per-tenant RAG isolation.

### How is model access governed?

The gateway serves only catalog-approved model IDs; adding a model requires a promotion request and a
governed provenance record (source, immutable ref, digest, license, risk tier), plus a model card. A
safety/jailbreak release gate must pass before promotion. See
[Model governance](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/model-governance.md).

### What compliance frameworks does it map to?

Controls are mapped to the OWASP LLM Top 10 ([mapping](owasp-llm-top-10-mapping.md)) and crosswalked to
NIST AI RMF, the EU AI Act, and ISO/IEC 42001 ([crosswalk](ai-governance-crosswalk.md)). These are
control mappings, not certifications; the kit ships the mechanisms an operator uses toward compliance.

## Troubleshooting

### `make quickstart` failed: where do I look?

The [Quickstart](quickstart.md) has a troubleshooting section covering the common Docker, `kind`,
`kubectl`, Helm, model-pull, and port-forward failures. For runtime-specific issues after the stack is
up, see [Inference runtime incident](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/incident-inference-runtime.md).

### A deploy was rejected by policy.

Kyverno enforces pod hardening, image signatures, and egress rules. See
[Policy-blocked deploy](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/policy-blocked-deploy.md).
