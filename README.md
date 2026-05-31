# AI Platform Ops Lab: Run Private LLMs and Coding Agents on Kubernetes

AI Platform Ops Lab is a hands-on operations lab for teams that want to run private LLMs and coding agents on Kubernetes. It starts locally on `kind`, then carries the same charts, policies, runbooks, and evidence checks into customer-owned clusters.

## Live Demo

<p align="center">
  <video src="docs/assets/ai-platform-ops-lab-demo.mp4" poster="docs/assets/ai-platform-ops-lab-demo-poster.png" controls muted playsinline width="100%" title="AI Platform Ops Lab live demo"></video>
</p>

[Watch or download the live demo](docs/assets/ai-platform-ops-lab-demo.mp4)

The demo video is generated from a real command run with `scripts/demo-live.sh`.

## Why It Matters

- Serve private models behind an OpenAI-compatible gateway using Ollama locally or vLLM on customer GPU clusters.
- Give coding agents isolated workspaces with storage, RBAC, default-deny networking, approved egress, and RAG access.
- Trace and govern every request with API keys, request IDs, sandbox IDs, redacted audit logs, metrics, and budget limits.
- Bring production controls into the lab: model governance, provenance, quotas, retention, SLOs, release gates, restore drills, chaos drills, SBOMs, signing, scanning, and evidence packs.

## Architecture

![AI Platform Ops Lab architecture](docs/assets/architecture.svg)

Requests enter the inference gateway at `POST /v1/chat/completions`. The gateway forwards the request to either Ollama or vLLM based on `RUNTIME_BACKEND`, records Prometheus metrics, and returns an OpenAI-compatible response. Argo CD reconciles platform add-ons and workloads from this repository. The default lab runs fully on `kind` with Ollama. Customers can apply the same charts and policies to their existing Kubernetes clusters and enable vLLM on GPU nodes they already operate.

Gateway requests are traceable by design. Callers can send `X-Request-ID`, `X-Sandbox-ID`, and W3C `traceparent`; the gateway returns and forwards those headers, emits structured JSON audit events, and records prompt length plus a SHA-256 fingerprint without logging raw prompt text.

Gateway and RAG business endpoints support API-key authentication. Local values enable it with a demo hash for `local-development-only`; customer values reference External Secrets-backed hash secrets. Health and metrics endpoints remain open for Kubernetes probes and in-cluster scraping.

Model use is governed at the gateway. Set `runtime.allowedModels` in Helm values, or `ALLOWED_MODELS` in the gateway environment, to reject unapproved model IDs before traffic reaches Ollama or vLLM.

Gateway admission controls also bound request cost and risk before runtime forwarding. Configure `admission.maxMessages`, `admission.maxPromptChars`, `admission.maxCompletionTokens`, and `admission.allowStreaming` in Helm values.

Gateway guardrails reject obvious credential material before prompts reach the runtime. Configure `guardrails.promptSecretDetection` in Helm values and keep it enabled for coding-agent workspaces.

Sandbox budgets provide an additional guardrail for lab usage. Configure `budget.requestLimit`, `budget.promptCharLimit`, and `budget.estimatedTokenLimit` in Helm values, then inspect current usage at `GET /v1/sandbox/budget` with the same `X-Sandbox-ID` used for inference traffic. Local and customer values use a Redis-compatible shared budget backend so multiple gateway replicas see the same counters.

Approved model metadata lives in `model-catalog/models.yaml` and is published to the cluster as the `ai-model-catalog` ConfigMap.

Coding agents can run inside the `agent-workspace` chart's namespace. The workspace includes quota, default limits, restricted pod security, namespace-scoped RBAC, a PVC for `/workspace`, default-deny networking, and approved egress to the inference gateway and RAG service. The RAG service returns retrieved platform context plus OpenAI-compatible `grounded_messages` for agents that need customer-approved context before calling the gateway. Local values use zero-dependency lexical retrieval; customer values enable an optional Qdrant vector-store profile in the `vector` namespace for larger approved knowledge bases.

## Prerequisites

Local validation needs Python 3, Docker, kind, kubectl, Helm, Go, and Syft. Full security and load-test validation also need Argo CD CLI, Cosign, Trivy, k6, kubeconform, and the Kyverno CLI.

Inspect the current workstation toolchain:

    make toolchain-doctor

Install the strict validation tools into `.tools/bin`:

    make toolchain-install
    export PATH="$PWD/.tools/bin:$PATH"

Generate a toolchain evidence report:

    make toolchain-report TOOLCHAIN_PROFILE=strict

Run a local static check:

    make validate

Run a stricter check that fails when optional production tools are missing:

    make validate-full

Run the same static readiness gates without relying on a live cluster:

    make production-check

Generate a customer-facing evidence pack:

    make evidence

Check release gates against eval, load, restore, toolchain, SLO, governance, and evidence-pack thresholds:

    make release-gate

Validate SLO objectives and error-budget evidence:

    make slo-check

Validate quota and chargeback governance:

    make quota-check

Validate approved external egress for coding-agent and tenant workspaces:

    make egress-check

Validate data retention and privacy governance:

    make retention-check

## Local Quick Start

Create a local cluster and bootstrap GitOps:

    make local-up
    make bootstrap-argocd
    make sync

Run a smoke test through the gateway:

    make smoke RUNTIME_BACKEND=ollama

The default local path uses Ollama with `qwen2.5:0.5b` for smoke testing. The smoke scripts send `PLATFORM_API_KEY`, defaulting to the local demo key `local-development-only`. A real model pull can take time and disk space; set `MODEL_ID` in the gateway values when you want to use a different model.

Run the traceable sandbox proof:

    make sandbox-smoke

This applies the `ai-sandbox` namespace controls, runs a restricted Kubernetes Job through the gateway, and verifies request, sandbox, and trace headers.

Create and validate a team tenant lab:

    make tenant-smoke

Generate customer tenant onboarding artifacts:

    make tenant-onboard

Generate the regulated/offline tenant profile with no external CIDR egress:

    make tenant-onboard-regulated

Validate RAG and coding-agent workspace access:

    make rag-smoke
    make agent-smoke

Validate the optional vector-store profile without needing a live Qdrant instance:

    helm template validate-qdrant charts/qdrant-vector-store --values clusters/customer/values/qdrant-vector-store.yaml
    helm template validate-rag charts/rag-service --values clusters/customer/values/rag-service.yaml

Run a safe recovery drill:

    make chaos-drill

Run customer dependency and capacity drills:

    DRILL=rag-service-rollout make chaos-drill
    DRILL=qdrant-vector-store-rollout make chaos-drill
    DRILL=vllm-runtime-rollout make chaos-drill
    DRILL=gpu-capacity-preflight RUN_SMOKE=0 make chaos-drill

## Customer-Owned Kubernetes

For an existing Kubernetes cluster, install Argo CD, update `gitops/argocd/root-app.yaml` to point at your repository URL, and sync the same applications. The `clusters/customer/` values are provider-neutral and assume the customer already supplies ingress, storage classes, optional GPU nodes, and any enterprise secret backend.

GPU scheduling is standard Kubernetes scheduling. NVIDIA clusters should expose `nvidia.com/gpu`; AMD clusters should expose `amd.com/gpu`. Label GPU nodes with `platform.ai/node-pool=gpu` and `platform.ai/gpu-vendor=<nvidia|amd>`, then use `clusters/customer/values/vllm-nvidia.yaml` or `clusters/customer/values/vllm-amd.yaml`.

The default customer vLLM profile runs multiple replicas with an HPA, PodDisruptionBudget, service-account token automount disabled, and topology spread constraints. The local profile keeps vLLM at zero replicas so CPU-only workstations can run the lab with Ollama.

Customer RAG values switch `retrieval.backend` to `qdrant` and deploy `charts/qdrant-vector-store` with persistent storage. Customers should size Qdrant storage, vector dimensions, and document ingestion to their own embedding model and approved knowledge pipeline.

For regulated or offline teams, use `tenants/onboarding/regulated-offline-coding-agents.yaml` or `make tenant-onboard-regulated`. It renders confidential tenant labels, disables external CIDR egress, disables default job-management RBAC, and keeps access limited to in-cluster DNS, gateway, and RAG paths.

## Restore Drills

Application-data restore verification is handled by the existing GitHub project `RamazanKara/restore-drill`. This repo consumes that tool through local wrapper scripts and a Kubernetes CronJob path. Velero is used separately for cluster resource and persistent-volume backup.

Run the local restore drill:

    make restore-drill RUNTIME=local

Run restore-drill plus the Velero disposable namespace scenario:

    make backup-drill

Restore evidence is written under `results/restore-drill/`.

## Load Testing

Run k6 load tests against the gateway:

    make loadtest

Results are written under `results/loadtest/` as JSON plus a Markdown summary.

## Evaluation Harness

Run repeatable prompt checks against the gateway:

    make eval

The default suite lives at `evals/smoke-suite.yaml`. A richer coding-agent suite lives at `evals/coding-agent-suite.yaml` and covers change planning, secret handling, prompt-injection boundaries, and incident triage. The wrapper writes JSON and Markdown evidence under `results/evals/`. Use `GATEWAY_URL=http://host:port make eval` when the gateway is already reachable.

Run the coding-agent suite explicitly:

    SUITE=evals/coding-agent-suite.yaml make eval

## Model Governance

Validate model lifecycle metadata, promotion requests, approved-only allowlists, and vLLM profile alignment:

    make model-check

Generate a JSON and Markdown governance report:

    make model-report

Reports are written under `results/model-catalog/`.

Validate approved model artifact provenance:

    make model-provenance-check
    make model-provenance-report

Reports are written under `results/model-provenance/`.

## Customer Evidence Pack

Generate a Markdown and JSON evidence pack before demos, release reviews, incident follow-ups, or restore-drill handoff:

    make evidence

After the local lab is synced, include live Kubernetes readiness checks:

    make evidence LIVE=1

Evidence packs are written under `results/evidence/` and summarize static controls, generated artifacts, and customer action items.

## Release Gates

Check whether current handoff evidence meets the local customer-readiness thresholds:

    make release-gate

Generate JSON and Markdown release-gate evidence:

    make release-report

Thresholds live in `slo/release-gates.yaml`; reports are written under `results/release-gate/`.

## SLO And Error Budgets

Customer-facing SLO objectives live in `slo/objectives.yaml`. They cover inference error rate and latency, smoke evaluation pass rate, restore verification, and coding-agent platform readiness.

    make slo-check
    make slo-report

Reports are written under `results/slo/`.

## Quota And Chargeback

Reviewed quota plans live in `governance/quota-plans.yaml`. They connect Kubernetes quotas, gateway sandbox budgets, workspace sizing, and required chargeback labels.

    make quota-check
    make quota-report

Reports are written under `results/quota/`.

## Egress Governance

External egress for coding-agent workspaces and tenant labs must be approved in `network/egress-catalog.yaml` and referenced with `catalogRef` before NetworkPolicies allow it.

    make egress-check
    make egress-report

## Data Retention

Retention and privacy controls live in `governance/data-retention.yaml`. They cover redacted audit logs, generated evidence, RAG knowledge, agent workspace data, and model governance records.

    make retention-check
    make retention-report

## Tenant Onboarding

Generate a customer-ready tenant package from a reviewed spec:

    make tenant-onboard

The default spec is `tenants/onboarding/coding-agents.yaml`. The generator writes a tenant manifest, agent workspace Helm values, and a short apply guide under `tenants/generated/`.

## Runbooks

Start with the [documentation map](docs/README.md). The main production checklist is the [production readiness matrix](docs/production-readiness.md), and operational procedures live under `runbooks/`.

Key runbooks:

| Need | Document |
| --- | --- |
| Operate gateway access, sandboxing, and budgets | [API access](runbooks/api-access.md), [traceable sandbox](runbooks/traceability-sandbox.md), [budget controls](runbooks/budget-controls.md) |
| Run coding-agent labs | [agent workspaces](runbooks/agent-workspaces.md), [tenant labs](runbooks/tenant-labs.md), [RAG service](runbooks/rag-service.md), [vector RAG](runbooks/vector-rag.md) |
| Govern customer handoff | [evidence packs](runbooks/evidence-pack.md), [release gates](runbooks/release-gates.md), [SLOs](runbooks/slo-error-budget.md), [validation toolchain](runbooks/validation-toolchain.md) |
| Prove resilience and recovery | [restore drills](runbooks/restore-drill.md), [chaos drills](runbooks/chaos-drills.md), [runtime incident response](runbooks/incident-inference-runtime.md) |
| Review security and compliance | [guardrails](runbooks/guardrails.md), [model governance](runbooks/model-governance.md), [model provenance](runbooks/model-provenance.md), [egress governance](runbooks/egress-governance.md), [data retention](runbooks/data-retention.md) |
