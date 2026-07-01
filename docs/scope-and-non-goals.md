# Scope and Non-Goals

This document draws an explicit boundary around what Private AI Platform Kit (v0.14.0) is and is not,
and then maps the controls it ships to the six AWS Well-Architected pillars. Use it to set
expectations before adoption and to sanity-check that the operator-owned gaps are understood.

The kit's deliverable boundary is fixed: "Kubernetes manifests, Helm charts, service code, validation
tooling, and operational runbooks" (see [README Support Boundaries](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/README.md) and
[ROADMAP Remaining External / Operator-Owned Work](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/ROADMAP.md)). Everything below is read against
that boundary.

## In Scope

The kit ships and validates the following, all runnable on a local `kind` cluster and portable to a
customer-owned cluster:

- An OpenAI-compatible inference gateway (`src/inference-gateway/app`) with API-key and optional
  JWT/JWKS auth, model allowlists, admission limits, prompt secret detection, sandbox budgets,
  canary/shadow progressive delivery, runtime failover, Prometheus metrics, and a tamper-evident
  SHA-256 audit chain.
- A RAG service (`src/rag-service`) with lexical retrieval and an optional Qdrant hybrid vector
  profile, plus OpenAI-compatible embeddings.
- GitOps via Argo CD (`deploy/gitops/argocd`, `deploy/clusters/{local,customer}`), Helm charts
  (`deploy/charts/`), and Kyverno policy-as-code (`deploy/policies/kyverno`).
- Observability (kube-prometheus-stack, Loki, Promtail, pushgateway, OpenCost), SLOs
  (`platform/slo`), governance (`platform/governance`), a model catalog (`platform/model-catalog`),
  egress catalog (`platform/network`), backup/restore drills (`deploy/backup`), and roughly 28
  operational runbooks (`runbooks/`).
- Validation tooling: API and config contract snapshots, evidence packs, release gates, supply-chain
  scanning, SBOMs, Cosign signing, and provenance attestations.

For the authoritative control-by-control breakdown with per-area validation commands, see the
[Production readiness matrix](production-readiness.md).

## Non-Goals

These are explicit non-goals. Each is something the kit deliberately does not do; in most cases it is
work the operator already owns or a separate product category. They are derived directly from the
README "Support Boundaries", the [Decision guide](decision-guide.md) "Poor Fit" list, and the ROADMAP
"Remaining External / Operator-Owned Work" section.

### Platform and infrastructure

- **It does not provision cloud infrastructure.** No Terraform/CloudFormation for VPCs, subnets, node
  pools, or managed databases. The customer profile assumes Kubernetes already exists
  ([README Customer-Owned Kubernetes](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/README.md)).
- **It does not operate your Kubernetes cluster.** Cluster lifecycle, upgrades, capacity, and on-call
  remain the operator's. "Production support without owning Kubernetes operations" is a Poor Fit in
  the [Decision guide](decision-guide.md).
- **It does not host customer models.** Model *weights* and LoRA/adapter *artifacts* are the
  customer's to host and pin; the kit ships serving flags, catalog governance, and a source-reference
  provenance digest the customer replaces with their own model-store checksum (ROADMAP `runtime`).

### Replacing platform services you already run

The README states the kit does not replace your identity provider, secret manager, logging stack,
backup platform, or incident process. Concretely:

- **Identity provider.** The gateway validates API-key hashes and optional JWT/JWKS, but the kit does
  not run an IdP. Customers wire it to their enterprise identity boundary (RS256/ES256 preferred) and
  rotate keys through External Secrets ([threat-model.md](threat-model.md) Required Customer
  Hardening; [runbooks/oidc-jwks-rotation.md](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/oidc-jwks-rotation.md)).
- **Secret manager.** The kit ships External Secrets *examples*
  (`deploy/clusters/customer/external-secrets.yaml`) and commits no runtime tokens; the enterprise
  backend is the operator's.
- **Logging/backup/incident platform.** Logs are Loki-ready and structured, restore drills and Velero
  examples exist (`deploy/backup`), and incident runbooks are provided, but centralizing logs,
  running scheduled production backups, and owning the incident process are operator responsibilities.

### Out-of-scope product surfaces

- **No multi-node distributed serving operator.** Multi-node serving needs the LeaderWorkerSet (or
  Ray) operator and per-cluster GPU topology. The kit ships the working LWS example and
  pipeline-parallel flags ([runbooks/gpu-capacity.md](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/gpu-capacity.md)); installing and
  sizing the operator is the operator's (ROADMAP `runtime`).
- **No general-purpose training or batch-inference platform.** "A general-purpose distributed
  training or batch inference platform" is a Poor Fit ([decision-guide.md](decision-guide.md)). The
  gateway exposes a `/v1/batches` API for inference batching, not a training/data platform.
- **No admin/usage console UI.** A standalone admin or usage console is a separate web application.
  The kit ships the `/v1/usage` data layer, the metrics, and the client SDK such a console would
  build on (ROADMAP `dx`).
- **No single-machine personal assistant.** "A single-machine personal Ollama setup" and "a hosted AI
  gateway with managed identity, billing, and support" are both Poor Fit
  ([decision-guide.md](decision-guide.md)). The kit is a Kubernetes operating model, not a desktop
  app or a managed SaaS.

### Operator-owned operational decisions

Some controls ship ready to use but are intentionally left in a safe default that the operator must
flip per environment (ROADMAP `security`):

- Flipping the encryption-at-rest Kyverno policy from `Audit` to `Enforce` is a per-environment
  decision; the policy and labels ship ready.
- Scheduling the age-based retention purge as a CronJob is a per-environment decision; the purge
  command ships ready.
- Replacing source-reference model digests with the customer's model-store digests before production
  ([production-readiness.md](production-readiness.md) Model provenance).

## Well-Architected Pillar Mapping

The table below maps the kit's concrete controls to the six
[AWS Well-Architected](https://docs.aws.amazon.com/wellarchitected/latest/framework/the-pillars-of-the-framework.html)
pillars. Each cell cites a real repo control, file, runbook, or validation command. Where the pillar
is fundamentally the operator's responsibility, the cell says so rather than overclaiming. This is a
self-assessment against the framework's structure, not an AWS review or certification.

| Pillar | Kit controls (with citation) | Operator-owned |
| --- | --- | --- |
| **Operational Excellence** | GitOps with Argo CD auto-sync (`deploy/gitops/argocd`, `deploy/clusters/{local,customer}`); ~28 runbooks (`runbooks/`); release gates that require current evidence (`platform/slo/release-gates.yaml`, `make release-gate-strict`); evidence packs (`make evidence`); chaos/upgrade drills ([runbooks/chaos-drills.md](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/chaos-drills.md), [runbooks/upgrade.md](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/upgrade.md)); API/config contract snapshots (`make api-contract`, `make config-contract`); tamper-evident SHA-256 audit chain in the gateway (`_chain_audit_event` in `src/inference-gateway/app/main.py`). | Owning the incident process, on-call, and change management; keeping evidence current per release. |
| **Security** | API-key + optional JWT/JWKS auth, model allowlists, admission limits, prompt secret detection, sandbox budgets (`src/inference-gateway/app`); prompt audit redaction (length + SHA-256 only, `test_audit_log_redacts_prompt_content`); default-deny NetworkPolicies and catalog-backed egress (`platform/network/egress-catalog.yaml`, Kyverno `ai-platform-restrict-egress-cidrs`); Kyverno pod hardening, read-only root FS, image-signature verification (`deploy/policies/kyverno`); keyless Cosign signing, Trivy HIGH/CRITICAL gates, SBOMs, provenance attestations; threat model ([threat-model.md](threat-model.md)). | Wiring to the enterprise IdP and secret manager; classifying ingested data; setting retention and residency; flipping encryption-at-rest policy to Enforce. |
| **Reliability** | Multi-replica gateway and runtimes with HPA, KEDA ScaledObjects, PodDisruptionBudgets, and topology spread ([production-readiness.md](production-readiness.md) Runtime high availability); runtime failover and circuit breaking in the gateway; shared Redis-backed sandbox budgets for multi-replica correctness (`test_redis_budget_tracker_shares_usage_across_tracker_instances`); restore drills (Redis AOF + Qdrant data-restore, `make restore-drill`) and Velero examples (`deploy/backup`); SLOs and burn-rate alerts (`platform/slo/objectives.yaml`, `make slo-check`). | Sizing min/max replicas and GPU inventory to SLOs; running scheduled production restore drills and validating recoverability of real data. |
| **Performance Efficiency** | Accelerator portability via vLLM profiles for CPU-off local, NVIDIA, and AMD ROCm (`deploy/clusters/customer/values/vllm-*.yaml`); multi-arch images (`linux/amd64`, `linux/arm64`, [decision-guide.md](decision-guide.md) Architecture Support); KEDA queue-depth autoscaling for gateway and vLLM (`deploy/charts/*/templates/scaledobject.yaml`); admission limits capping prompt size, message count, completion tokens (`test_admission_policy_rejects_unsafe_or_expensive_requests`); k6 load tests and latency SLOs (`make loadtest`, `inference-latency` objective); canary/shadow progressive delivery in the gateway. | Tuning replica counts, context length, tensor parallelism, GPU requests, and autoscaling thresholds to the customer cluster. |
| **Cost Optimization** | OpenCost app (`deploy/observability/applications.yaml`, `deploy/clusters/local/apps.yaml`); required owner/cost/environment/sandbox labels enforced by Kyverno ([production-readiness.md](production-readiness.md) Cost controls); per-sandbox budgets and quota/chargeback plans (`platform/governance/quota-plans.yaml`, `make quota-check`); the `/v1/usage` usage+cost API in the gateway; KEDA scale-down (and optional scale-to-zero for the vLLM GPU runtime, `deploy/charts/vllm/values.yaml` `minReplicaCount: 0`). | Mapping cost labels to a chargeback/showback taxonomy; setting tenant quota plans and GPU budget ceilings. |
| **Sustainability** | Pinned Alpine Python runtime images that exclude test-only dependencies (README Evidence Commands), reducing image footprint; multi-arch images enabling efficient arm64 (Graviton/Ampere) nodes ([decision-guide.md](decision-guide.md)); KEDA queue-depth autoscaling and optional GPU scale-to-zero to avoid idle accelerator draw (`deploy/charts/vllm/values.yaml`); small default local model (`qwen2.5:0.5b`) to keep laptop demos light (README). | Right-sizing GPU node pools, choosing low-carbon regions, and selecting model sizes proportional to the task — all per-cluster operator decisions. |

## Related Reading

- [Decision guide](decision-guide.md) — best-fit / poor-fit and a comparison against adjacent tools.
- [Production readiness matrix](production-readiness.md) — the control-by-control source of truth.
- [Threat model](threat-model.md) — assets, trust boundaries, and required customer hardening.
- [Roadmap](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/ROADMAP.md) — the operator-owned work that remains external to the kit.
