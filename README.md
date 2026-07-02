# Private AI Platform Kit: Coding Agents on Your Kubernetes — With Receipts

[![CI](https://github.com/RamazanKara/private-ai-platform-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/RamazanKara/private-ai-platform-kit/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/RamazanKara/private-ai-platform-kit)](https://github.com/RamazanKara/private-ai-platform-kit/releases)
[![License](https://img.shields.io/github/license/RamazanKara/private-ai-platform-kit)](LICENSE)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21039342.svg)](https://doi.org/10.5281/zenodo.21039342)
![Kubernetes](https://img.shields.io/badge/Kubernetes-GitOps-326CE5)
![Helm](https://img.shields.io/badge/Helm-charts-0F1689)
![Python](https://img.shields.io/badge/Python-3.12+-3776AB)

Your team wants coding agents. Your security review asks three questions: **where does the generated code execute, what can it reach, and can you prove what it did?** This kit answers all three as running code, on your own cluster, with no cloud dependency:

- **Kernel-isolatable sandboxes as the standard runtime** — every workspace is a hardened [kubernetes-sigs/agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) pod: non-root, read-only rootfs, no ambient credentials, and a short-lived audience-bound token instead of long-lived secrets.
- **Fail-closed egress** — default-deny networking where every exception is a reviewed, expiring catalog entry. Exfiltration attempts don't get logged and forgiven; they don't connect.
- **Receipts, not just logs** — every governed model call lands on a tamper-evident hash chain as an allowed/denied receipt, crosswalked to the EU AI Act, NIST AI RMF, and ISO/IEC 42001 for the auditors you'll meet anyway.

<p align="center">
  <img src="docs/assets/private-ai-platform-kit-demo.gif" alt="Terminal demo: hardened agent sandbox, blocked exfiltration, allow/deny receipts, green evidence pack" width="100%">
</p>

The cut above is scripted from real output ([scripts/demo-live.sh](scripts/demo-live.sh), recorded via [scripts/demo.tape](scripts/demo.tape)). An **[unscripted real run](docs/assets/private-ai-platform-kit-demo-real.gif)** (3× speed, ~443 KB) shows the live cluster doing the same: the hardened sandbox, a blocked exfiltration attempt — including litellm's own telemetry callout dying against default-deny — the real coding agent through the governed gateway, and the receipts on the chain ([scripts/demo-real.sh](scripts/demo-real.sh) + [scripts/demo-real.tape](scripts/demo-real.tape)). Run it yourself with `make agent-sandbox-demo`.

Under the agents sits a complete private-LLM platform: an OpenAI-compatible gateway (auth, admission, per-sandbox budgets, guardrails), vLLM and Ollama serving from the same charts, RAG with per-tenant isolation, GitOps delivery, and evidence packs an auditor can verify offline. It starts local-first on a laptop `kind` cluster and moves to customer-owned clusters with GPU nodes using the same repo layout — the operating model of a production AI platform, without depending on a specific cloud provider.

Current release: `v0.14.0`. Maturity: reference implementation and customer lab; production handoff requires current strict evidence, customer identity/secrets integration, capacity sizing, and backup validation.

**Who it's for:** platform / SRE teams evaluating a private-AI stack → start with the [Decision guide](docs/decision-guide.md); operators running it → [Runbooks](runbooks/README.md); security & compliance reviewers → [Security overview](docs/security-overview.md), [OWASP LLM Top 10 mapping](docs/owasp-llm-top-10-mapping.md), and the [Threat model](docs/threat-model.md).

[Docs site](https://ramazankara.github.io/private-ai-platform-kit/) · [Quickstart](docs/quickstart.md) · [Architecture](docs/architecture.md) · [Decision guide](docs/decision-guide.md) · [Production readiness](docs/production-readiness.md) · [Docs map](docs/README.md)

## Quickstart

Docker is the only prerequisite. One guided command creates a local `kind` cluster, installs the agent-sandbox controller, bootstraps Argo CD, syncs the stack, and runs an Ollama-backed smoke test:

```bash
make quickstart
```

Then watch a real coding agent work inside the governed sandbox:

```bash
make agent-sandbox-demo
```

See [docs/quickstart.md](docs/quickstart.md) for expected output, timing, disk needs, and troubleshooting, or [Run It Locally](#run-it-locally) below for the step-by-step path.

## What You Get

- **Inference gateway** — OpenAI-compatible chat, embeddings, moderations, and batch endpoints; API-key and JWT/JWKS auth with per-tenant sandbox binding; model allowlists, admission limits, per-sandbox budgets, and rate limiting; input prompt-secret detection and a response-path output guardrail (flag / redact / block); a shared Redis response cache; progressive delivery (canary + shadow) and cross-runtime failover; a tamper-evident audit chain that never logs raw prompt text.
- **Model serving** — Ollama for laptop/`kind` and vLLM for NVIDIA or AMD GPUs from the same charts, with first-class prefix caching, FP8/AWQ quantization, guided/speculative decoding, MIG guidance, and HPA/KEDA, PodDisruptionBudgets, and topology spread.
- **RAG** — hybrid dense + lexical retrieval with an optional cross-encoder reranker and per-tenant retrieval isolation; a local lexical profile or a persistent Qdrant vector store; RAGAS-style faithfulness and context-precision evals.
- **Coding-agent workspaces** — hardened kubernetes-sigs/agent-sandbox pods as the standard runtime (ADR 0010) inside locked-down namespaces with PVC storage, RBAC, quotas, default-deny networking, catalog-approved egress, and gated RAG access; kernel-isolation runtime-class support, short-lived audience-bound workspace credentials instead of long-lived secrets, and per-action allow/deny receipts on the tamper-evident audit chain — demoed end-to-end with a real coding agent via `make agent-sandbox-demo`.
- **Governance & compliance** — approved-only model catalog with promotion requests, provenance, and per-model model cards; a safety / jailbreak release gate; production drift monitoring; an OWASP LLM Top 10 mapping and a NIST AI RMF / EU AI Act / ISO 42001 crosswalk.
- **Operations & evidence** — SLOs and release gates, quota/chargeback, data retention, egress governance; Prometheus + Grafana, Tempo tracing, Loki logs, and cost/OpenCost dashboards; Pod Security Admission, an opt-in encryption-in-transit overlay, and optional Falco runtime detection; restore and chaos drills plus a disaster-recovery runbook; SBOMs, Trivy scans, Cosign-signed images, provenance attestations, OpenSSF Scorecard, and evidence packs.

## How It Works

![Private AI Platform Kit architecture](docs/assets/architecture.svg)

Requests enter the inference gateway at `POST /v1/chat/completions`. The gateway authenticates the caller, enforces model allowlists and admission limits, applies input and output guardrails, routes to Ollama or vLLM (with failover), records Prometheus metrics, and emits redacted audit events. Callers can pass `X-Request-ID`, `X-Sandbox-ID`, and W3C `traceparent`; the gateway returns and forwards those headers without logging raw prompt text.

The local lab runs fully on `kind`. Customer clusters keep the same repo structure and replace only the platform services they already operate: ingress, storage classes, secret backends, logging, observability, and GPU node pools. Per-profile diagrams (local, customer GPU, and regulated-offline) and an end-to-end request-flow walkthrough live in [docs/architecture.md](docs/architecture.md).

## Run It Locally

For a guided first run, use `make quickstart` (above). Use `QUICKSTART_INSTALL_TOOLS=1 make quickstart` to install optional validation CLIs into `.tools/bin`, or `QUICKSTART_DIRECT_APPLY=1 make quickstart` to use direct Helm apply instead of Argo CD for a workstation check.

Validate the repo without a live cluster:

```bash
make validate          # tests, lint, type-check, chart render, contracts, governance
make production-check   # static production-readiness checks
```

Start the local platform and run an Ollama-backed smoke test step by step:

```bash
make local-up
make bootstrap-argocd
make sync
make smoke RUNTIME_BACKEND=ollama
```

The default local model is `qwen2.5:0.5b`, a fast non-reasoning model that keeps the laptop CPU smoke quick; the larger `qwen3.5:0.8b` reasoning model is the customer Ollama profile default. A real model pull can take time and disk space on the first run.

For the full local path — sandbox tracing, RAG, coding-agent workspaces, restore drills, evals, load tests, and release gates — follow [docs/getting-started.md](docs/getting-started.md).

## Support Boundaries

This project provides Kubernetes manifests, Helm charts, service code, validation tooling, and operational runbooks. It does not provision cloud infrastructure, operate your Kubernetes cluster, host customer models, or replace your identity provider, secret manager, logging stack, backup platform, or incident process. See [docs/scope-and-non-goals.md](docs/scope-and-non-goals.md) for the full scope boundary, and [docs/decision-guide.md](docs/decision-guide.md) to decide whether the kit is a fit.

## Customer-Owned Kubernetes

The customer profile assumes Kubernetes already exists. Install Argo CD, configure the customer GitOps overlay, and apply the customer values under [deploy/clusters/customer](deploy/clusters/customer/).

```bash
make customer-overlay \
  CUSTOMER_REPO_URL=https://github.com/<customer>/<repo>.git \
  CUSTOMER_REVISION=v0.14.0 \
  CUSTOMER_GPU_PROFILE=nvidia
```

NVIDIA clusters should expose `nvidia.com/gpu`; AMD clusters should expose `amd.com/gpu`. Label GPU nodes with `platform.ai/node-pool=gpu` and `platform.ai/gpu-vendor=<nvidia|amd>`, then use the [NVIDIA](deploy/clusters/customer/values/vllm-nvidia.yaml) or [AMD](deploy/clusters/customer/values/vllm-amd.yaml) vLLM profile (quantized [FP8](deploy/clusters/customer/values/vllm-nvidia-fp8.yaml) / [AWQ](deploy/clusters/customer/values/vllm-nvidia-awq.yaml) variants are also provided). The default customer vLLM profile targets `Qwen/Qwen3-Coder-Next`; tune replica count, context length, tensor parallelism, and GPU requests before production use.

## Docs

Popular starting points — see the [full documentation map](docs/README.md) for everything:

| Need | Start here |
| --- | --- |
| First local run | [Quickstart](docs/quickstart.md) |
| Full local workflow | [Getting started](docs/getting-started.md) |
| How the pieces fit | [Architecture](docs/architecture.md) |
| Is this for you | [Decision guide](docs/decision-guide.md) |
| Production controls | [Production readiness matrix](docs/production-readiness.md) |
| Security & compliance | [Security overview](docs/security-overview.md) · [OWASP LLM Top 10](docs/owasp-llm-top-10-mapping.md) · [Threat model](docs/threat-model.md) |
| Operations | [Runbooks](runbooks/README.md) |
| Design decisions | [Architecture decision records](docs/adr/README.md) |
| Contributing / Security policy | [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) |

## Repo Map

| Path | Purpose |
| --- | --- |
| `deploy/charts/` | Helm charts for gateway, runtimes, RAG, vector store, budget Redis, agent workspaces, and the umbrella install chart |
| `deploy/clusters/local/` | Local `kind` and Argo CD values |
| `deploy/clusters/customer/` | Provider-neutral customer cluster values and the encryption-in-transit overlay |
| `src/` | Gateway and RAG service code |
| `platform/api-contracts/`, `platform/config-contracts/` | Versioned OpenAPI and runtime-config snapshots for customer-facing services |
| `platform/governance/`, `platform/model-catalog/`, `platform/network/`, `platform/slo/` | Reviewed policy, model catalog + cards, and evidence inputs |
| `runbooks/` | Operational procedures and incident drills |
| `docs/` | Documentation site sources, ADRs, and architecture diagrams |
| `results/` | Sample evidence artifacts; generated reports are ignored by default |

## Evidence & Supply Chain

Representative evidence commands (see the [release-gates](runbooks/release-gates.md) and [evidence-pack](runbooks/evidence-pack.md) runbooks for the full set):

```bash
make evidence               # generate the customer evidence pack
make release-gate-strict    # gate on current eval, load, safety, SLO, supply-chain, and restore evidence
make eval-local             # scored evals against an ephemeral mock runtime
make supply-chain-check     # validate local image SBOM/SARIF/checksum evidence
```

Runtime images use a pinned Alpine Python base and exclude test-only dependencies. CI builds and pushes gateway and RAG images, packages Helm charts as OCI artifacts, generates SBOMs, fails on high/critical Trivy findings, uploads SARIF, signs immutable image digests with Cosign, and publishes downloadable supply-chain evidence for release reviews.

## Trademark Notice

Kubernetes is a registered trademark of The Linux Foundation. Private AI Platform Kit is not affiliated with or endorsed by The Linux Foundation.
