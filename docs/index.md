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

- OpenAI-compatible gateway with API-key + JWT/JWKS auth, model allowlists, admission limits, per-sandbox budgets and rate limiting, input prompt-secret detection and a response-path **output guardrail**, a shared response cache, canary/shadow delivery, and a tamper-evident audit chain.
- A local Ollama profile and vLLM profiles for NVIDIA/AMD GPUs from the **same** charts, with prefix caching, FP8/AWQ quantization, and guided/speculative decoding.
- RAG with hybrid dense + lexical retrieval, an optional cross-encoder reranker, per-tenant retrieval isolation, and RAGAS-style faithfulness evals.
- Locked-down coding-agent workspaces: namespace isolation, RBAC, quotas, default-deny networking, governed egress, and RAG access.
- Governance & compliance: approved-only model catalog with promotion requests, reproducible provenance digests, model cards, a safety/jailbreak release gate, and an OWASP LLM Top 10 + NIST/EU-AI-Act/ISO-42001 crosswalk.
- Operations & evidence: SLOs and release gates, quota/chargeback, retention, egress governance; Prometheus + Grafana, Tempo tracing, Loki, and cost/OpenCost dashboards; Pod Security Admission, encryption-in-transit overlay, and Falco; restore/chaos drills and a disaster-recovery runbook; SBOMs, scans, signed images, provenance attestations, OpenSSF Scorecard, and evidence packs.

## How it works

![Private AI Platform Kit architecture](assets/architecture.svg)

Requests enter the inference gateway at `POST /v1/chat/completions`. The gateway authenticates the caller, enforces model allowlists and admission limits, applies input and output guardrails, routes to Ollama or vLLM (with failover), records Prometheus metrics and OTLP traces, and emits redacted audit events. The local lab runs fully on `kind`; customer clusters keep the same repo structure and replace only the platform services they already operate. Per-profile diagrams and an end-to-end request walkthrough are in [Architecture](architecture.md).

!!! tip "Maturity"
    Current release `v0.13.0` — reference implementation and customer lab. Production handoff requires current strict evidence, customer identity/secrets integration, capacity sizing, and backup validation. See [Production readiness](production-readiness.md).

---

Stewarded by [fluentorbit](https://fluentorbit.de). Vendor-neutral and Apache-2.0 licensed. Found a security issue? See [Security](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/SECURITY.md).
