# Private AI Platform Kit

A runnable, vendor-neutral Kubernetes platform for **private LLMs, RAG, and coding-agent workspaces**. It starts on a local `kind` cluster with Ollama, then uses the **same** Helm charts, GitOps layout, policies, runbooks, and evidence checks on customer-owned clusters with vLLM and GPU nodes.

It is for teams that want the operating model of a production AI platform without depending on a specific cloud provider.

<div class="grid cards" markdown>

- :material-rocket-launch: **[Tutorials](quickstart.md)** — start here. Go from nothing to a working private chat on your laptop.
- :material-wrench: **[How-to guides](getting-started.md)** — task recipes: deploy to a customer cluster, run evals, generate evidence, handle incidents.
- :material-book-open-variant: **[Reference](production-readiness.md)** — the production-readiness matrix, benchmarks, contracts, and release verification.
- :material-lightbulb: **[Explanation](decision-guide.md)** — is this for you, how it works, the threat model, and the proof behind the claims.

</div>

## What you get

- OpenAI-compatible gateway for private chat-completion traffic, with model allowlists, admission limits, redacted audit logs, and optional JWT/JWKS auth.
- A local Ollama profile for fast laptop demos and vLLM profiles for NVIDIA or AMD GPU clusters — the same charts in both.
- Locked-down coding-agent workspaces: namespace isolation, RBAC, quotas, default-deny networking, governed egress, and RAG access.
- Model governance with approved-only allowlists, promotion requests, **reproducible** provenance digests, and eval suites.
- Operational controls for SLOs, release gates, quota and chargeback, retention, egress governance, restore and chaos drills, evidence packs, SBOMs, scans, signed images, provenance attestations, and OpenSSF Scorecard.

## How it works

![Private AI Platform Kit architecture](assets/architecture.svg)

Requests enter the inference gateway at `POST /v1/chat/completions`. The gateway forwards to Ollama or vLLM based on `RUNTIME_BACKEND`, enforces model allowlists and admission limits, records Prometheus metrics, and emits redacted audit events. The local lab runs fully on `kind`; customer clusters keep the same repo structure and replace only the platform services they already operate.

!!! tip "Maturity"
    Current release `v0.10.0` — reference implementation and customer lab. Production handoff requires current strict evidence, customer identity/secrets integration, capacity sizing, and backup validation. See [Production readiness](production-readiness.md).

---

Stewarded by [fluentorbit](https://fluentorbit.de). Vendor-neutral and Apache-2.0 licensed. Found a security issue? See [Security](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/SECURITY.md).
