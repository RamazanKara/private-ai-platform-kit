# Production Readiness Matrix

This kit is local-first, but the controls are shaped like customer production controls. The local stack is the reference implementation. Customer clusters should keep the same interfaces and replace only the platform services they already operate, such as ingress, storage, secrets, logging, and GPU node pools.

## Required Controls

| Area | Local lab implementation | Customer cluster expectation | Validation |
| --- | --- | --- | --- |
| Runtime isolation | Ollama in `ollama`, optional vLLM in `vllm`, gateway in `inference` | Dedicated namespaces and runtime service accounts | `make smoke RUNTIME_BACKEND=ollama` |
| Accelerator portability | vLLM profiles for CPU-off local lab, NVIDIA, and AMD ROCm | Customer clusters expose `nvidia.com/gpu` or `amd.com/gpu` | `make production-check` |
| Runtime high availability | Gateway replicas, vLLM replicas, HPA, PDBs, and topology spread | Size min/max replicas to SLOs and GPU inventory | `helm template` in `make validate` |
| Traceability | `X-Request-ID`, `X-Sandbox-ID`, optional `traceparent`, JSON audit events | Forward the same headers through ingress and log pipeline | `make smoke`, `make sandbox-smoke`, gateway tests |
| API authentication | Gateway and RAG business endpoints require API keys in local/customer values | Back hashes with customer secret manager and rotate keys through External Secrets | gateway/RAG auth tests, `make smoke`, `make rag-smoke` |
| API contracts | `api-contracts/` stores OpenAPI snapshots for gateway and RAG with stable operation IDs and auth declarations | Review contract diffs before changing customer-facing routes, request schemas, or auth semantics | `make api-contract`, `make api-contract-update` |
| Configuration contracts | `config-contracts/` stores service runtime env snapshots and checks them against Python settings, Helm templates, and chart defaults | Review config diffs before changing customer overlays, secrets, budgets, retrieval settings, or runtime endpoints | `make config-contract`, `make config-contract-update` |
| Prompt privacy | Audit logs include prompt length and SHA-256 only | Do not log raw prompt text by default | `test_audit_log_redacts_prompt_content` |
| Data retention | `governance/data-retention.yaml` covers audit logs, generated evidence, RAG knowledge, agent workspace data, and model governance records | Align retention days and classification to customer policy | `make retention-check`, `make retention-report` |
| Model governance | Gateway `ALLOWED_MODELS` rejects unapproved model IDs | Maintain an approved model catalog per environment | `test_chat_completion_rejects_disallowed_model` |
| Model catalog | `model-catalog/models.yaml` and cluster ConfigMap | Treat model additions as reviewed changes | `make production-check` |
| Model lifecycle | Approved models require promotion requests, evidence references, runtime metadata, and approved-only allowlists | Review promotion requests before adding models to gateway allowlists | `make model-check`, `make model-report` |
| Model provenance | `governance/model-provenance.yaml` requires source URI, immutable ref, digest, license, risk, data class, and serving profiles | Replace source-reference digests with customer model-store artifact digests before production use | `make model-provenance-check`, `make model-provenance-report` |
| Admission control | Gateway caps message count, prompt size, completion tokens, temperature, and streaming | Tune limits by sandbox and runtime capacity | `test_admission_policy_rejects_unsafe_or_expensive_requests` |
| Prompt secret detection | Gateway rejects prompts that match configured credential patterns before runtime forwarding | Keep enabled for coding-agent workspaces and tune patterns only after review | gateway guardrail tests, `make production-check` |
| Validation toolchain | `tools/validation-toolchain.yaml` declares `validate`, `local`, and `strict` profiles with a pinned Linux/CI installer | Install the strict profile before customer handoff or production-readiness sign-off | `make toolchain-install`, `make toolchain-doctor`, `make validate-full` |
| SLO and error budget | `slo/objectives.yaml` defines inference, eval, restore, and coding-agent platform objectives with alert references | Align targets to the customer's contract and review burn-rate alerts | `make slo-check`, `make slo-report` |
| Sandbox budgets | Gateway enforces request, prompt-character, and estimated-token ceilings by `X-Sandbox-ID` | Size limits by tenant and review overage events | `test_sandbox_budget_status_and_request_limit_rejection` |
| Shared budget backend | Local/customer values use Redis-compatible shared counters for multi-replica gateways | Replace bundled Redis with customer managed Redis when available | `test_redis_budget_tracker_shares_usage_across_tracker_instances` |
| Quota and chargeback | `governance/quota-plans.yaml` connects tenant quotas, gateway budgets, workspace sizing, and chargeback labels | Align quota plans to customer showback or chargeback policy before onboarding tenants | `make quota-check`, `make quota-report` |
| Sandbox isolation | `ai-sandbox` namespace, quota, limits, default-deny network policy | Per-team sandbox namespaces with quotas and egress allowlists | `make sandbox-smoke` |
| Tenant labs | `make tenant-up` and `make tenant-smoke` create team namespaces with quota, RBAC, trace contract, and network controls | One namespace per team or approved experiment boundary | `make tenant-smoke` |
| Tenant onboarding | `TenantOnboarding` spec renders tenant controls and matching agent workspace values | Review generated quota, RBAC, PVC, storage, and egress before apply | `make tenant-onboard`, `scripts/tenant-onboard.py --check` |
| Regulated offline tenant profile | `tenants/onboarding/regulated-offline-coding-agents.yaml` renders confidential, no-external-egress agent controls | Use for offline or regulated teams and add egress only through reviewed catalog-backed changes | `make tenant-onboard-regulated`, `make production-check` |
| RAG service | Local retrieval service returns platform context and OpenAI-compatible grounded messages | Replace or extend the knowledge set with customer-approved internal docs | `make rag-smoke`, RAG service tests |
| Vector RAG profile | `charts/qdrant-vector-store` and customer RAG values provide a persistent Qdrant backend | Size storage, vector dimensions, and ingestion to the customer's embedding model and approved document pipeline | `make production-check`, Qdrant/RAG Helm renders |
| Agent workspaces | `agent-workspace` chart creates a locked-down namespace, PVC, RBAC, trace contract, and approved egress for coding agents | One workspace per team, project, or agent boundary with customer-approved external egress | `make agent-smoke` |
| Egress governance | `network/egress-catalog.yaml` requires external agent egress to reference approved catalog entries | Review and expire Git, package mirror, artifact, and ticketing egress entries | `make egress-check`, `make egress-report` |
| Chaos drills | Safe rollout drills for gateway, budget Redis, Ollama, RAG, Qdrant, vLLM, and GPU capacity preflight | Run after platform upgrades and before customer demos or maintenance windows | `make chaos-drill`, `DRILL=gpu-capacity-preflight RUN_SMOKE=0 make chaos-drill` |
| Evaluation harness | `evals/smoke-suite.yaml`, `evals/coding-agent-suite.yaml`, and `make eval` for repeatable prompt and coding-agent checks | Maintain environment-specific suites and keep summaries as release evidence | `scripts/eval-suite.py --check-config` |
| Release gates | `slo/release-gates.yaml` enforces eval, load, restore, strict toolchain, SLO, governance, and evidence-pack thresholds | Run strict gates before demos, releases, restore reviews, and production-readiness handoff so checked-in sample evidence cannot pass | `make release-gate`, `make release-gate-strict`, `make release-report-strict` |
| Autoscaling | KEDA ScaledObject from Prometheus request rate | Tune thresholds to customer SLOs and GPU capacity | `helm template` in `make validate` |
| Observability | Prometheus metrics, Grafana dashboard, Loki-ready structured logs | Centralize metrics, logs, and alerts | `make validate`, dashboards in `observability/` |
| Policy as code | Kyverno required labels, resources, pod hardening, image signature audit | Enforce on AI namespaces and exclude platform operators | `make policy-test` when Kyverno CLI is installed |
| Cost controls | Required owner/cost/environment/sandbox labels and OpenCost app | Map labels to chargeback/showback taxonomy | `make validate` YAML checks |
| Secret handling | External Secrets examples and no committed runtime tokens | Replace local Kubernetes provider with enterprise backend | `clusters/customer/external-secrets.yaml` |
| Supply chain | Pinned Alpine runtime images, runtime-only Python dependencies, high/critical Trivy failure gates, SBOMs, Cosign digest signing, workflow artifacts, and release asset upload in CI | Promote only immutable signed/scanned image digests with downloadable evidence | `make image-scan`, GitHub Actions image job |
| Backup and restore | `restore-drill` application-data validation and Velero examples | Run scheduled restore evidence for each critical data store | `make restore-drill`, `make backup-drill` |
| Load testing | k6 chat-completion scenario with sandbox tags | Store summaries and compare against SLOs | `make loadtest` |
| Evidence pack | Static customer handoff report plus optional live Kubernetes readiness checks | Attach reports to release, demo, restore drill, or incident review evidence | `make evidence`, `make evidence LIVE=1` |

## Production Promotion Checklist

- Keep one namespace per runtime or tenant boundary.
- Keep `platform.ai/owner`, `platform.ai/cost-center`, `platform.ai/environment`, and `platform.ai/sandbox-id` on every AI workload.
- Route every request with `X-Request-ID` and `X-Sandbox-ID`; propagate `traceparent` when an upstream trace exists.
- Require `X-API-Key` or Bearer API keys for gateway and RAG business endpoints; store only SHA-256 hashes in Kubernetes Secrets.
- Confirm `make api-contract` passes and any OpenAPI snapshot diff is intentional before customer integration or release review.
- Confirm `make config-contract` passes and any runtime configuration snapshot diff is intentional before customer overlay changes.
- Confirm audit logs do not contain raw prompt or completion text unless a customer has explicitly approved that behavior.
- Confirm data retention policy and generated evidence retention match customer requirements.
- Confirm `ALLOWED_MODELS` contains only approved model IDs for the environment.
- Confirm each approved model has a promotion request and evidence references.
- Confirm each approved model has reviewed artifact provenance and customer-pinned digests before production use.
- Confirm sandbox budget limits are enabled and sized for each lab or tenant.
- Confirm the gateway budget backend is shared when running more than one gateway replica.
- Confirm reviewed quota plans match tenant ResourceQuota, gateway budget ceilings, and chargeback labels.
- Confirm each tenant namespace has quota, LimitRange, default-deny NetworkPolicy, trace contract, and required labels.
- Confirm generated tenant onboarding artifacts have been reviewed before applying them to a customer cluster.
- Confirm regulated/offline tenant profiles have no external CIDR egress and carry compliance/data-classification labels.
- Confirm each coding-agent workspace has quota, workspace storage, default-deny networking, gateway/RAG access, and no cluster-wide RBAC.
- Confirm any external coding-agent egress has an approved, non-expired `catalogRef`.
- Confirm RAG knowledge is approved for the environment and RAG audit logs contain query hashes, not raw private context.
- Confirm vector RAG collections use approved embeddings, matching dimensions, persistent storage, and customer backup/retention controls.
- Confirm chaos drills pass for gateway, RAG, vector store, runtime backend, budget backend, and GPU capacity before demos or releases.
- Confirm NVIDIA or AMD accelerator profiles match the customer's exposed GPU resource names.
- Confirm vLLM replica, HPA, PDB, and topology spread settings match GPU capacity.
- Confirm admission limits are set for each environment and match expected model capacity.
- Confirm prompt secret detection is enabled for coding-agent and tenant workspaces.
- Confirm `make toolchain-install` has run and `make toolchain-doctor TOOLCHAIN_PROFILE=strict` passes before strict customer handoff.
- Confirm `make slo-check` passes against current load, eval, restore, and evidence-pack artifacts.
- Confirm NetworkPolicies allow only expected ingress and egress paths.
- Confirm restore-drill evidence is generated on schedule and retained according to the customer's audit policy.
- Confirm runtime images exclude test-only dependencies, `make image-scan` passes, and CI produces SBOMs, fails on high/critical image vulnerabilities, signs immutable image digests, and publishes downloadable supply-chain evidence before promotion.
- Confirm evaluation summaries pass for the selected model and suite.
- Confirm coding-agent evaluation cases cover change planning, secret handling, prompt-injection boundaries, and incident triage.
- Confirm load-test results meet customer SLOs for latency, error rate, and throughput.
- Confirm `make release-gate-strict` passes against current handoff evidence without falling back to checked-in samples.
- Confirm an evidence pack has been generated and attached to the customer handoff or release review.
