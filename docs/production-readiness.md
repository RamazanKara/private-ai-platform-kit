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
| API contracts | `platform/api-contracts/` stores OpenAPI snapshots for gateway and RAG with stable operation IDs and auth declarations | Review contract diffs before changing customer-facing routes, request schemas, or auth semantics | `make api-contract`, `make api-contract-update` |
| Configuration contracts | `platform/config-contracts/` stores service runtime env snapshots and checks them against Python settings, Helm templates, and chart defaults | Review config diffs before changing customer overlays, secrets, budgets, retrieval settings, or runtime endpoints | `make config-contract`, `make config-contract-update` |
| Prompt privacy | Audit logs include prompt length and SHA-256 only | Do not log raw prompt text by default | `test_audit_log_redacts_prompt_content` |
| Data retention | `platform/governance/data-retention.yaml` covers audit logs, generated evidence, RAG knowledge, agent workspace data, and model governance records | Align retention days and classification to customer policy | `make retention-check`, `make retention-report` |
| Model governance | Gateway `ALLOWED_MODELS` rejects unapproved model IDs | Maintain an approved model catalog per environment | `test_chat_completion_rejects_disallowed_model` |
| Model catalog | `platform/model-catalog/models.yaml` and cluster ConfigMap | Treat model additions as reviewed changes | `make production-check` |
| Model lifecycle | Approved models require promotion requests, evidence references, runtime metadata, and approved-only allowlists | Review promotion requests before adding models to gateway allowlists | `make model-check`, `make model-report` |
| Model provenance | `platform/governance/model-provenance.yaml` requires source URI, immutable ref, digest, license, risk, data class, and serving profiles | Replace source-reference digests with customer model-store artifact digests before production use | `make model-provenance-check`, `make model-provenance-report` |
| Admission control | Gateway caps message count, prompt size, completion tokens, temperature, and streaming | Tune limits by sandbox and runtime capacity | `test_admission_policy_rejects_unsafe_or_expensive_requests` |
| Prompt secret detection | Gateway rejects prompts that match configured credential patterns before runtime forwarding | Keep enabled for coding-agent workspaces and tune patterns only after review | gateway guardrail tests, `make production-check` |
| Validation toolchain | `platform/tools/validation-toolchain.yaml` declares `validate`, `local`, and `strict` profiles with a pinned Linux/CI installer | Install the strict profile before customer handoff or production-readiness sign-off | `make toolchain-install`, `make toolchain-doctor`, `make validate-full` |
| SLO and error budget | `platform/slo/objectives.yaml` defines inference, eval, restore, and coding-agent platform objectives with alert references | Align targets to the customer's contract and review burn-rate alerts | `make slo-check`, `make slo-report` |
| Sandbox budgets | Gateway enforces request, prompt-character, and estimated-token ceilings by `X-Sandbox-ID` | Size limits by tenant and review overage events | `test_sandbox_budget_status_and_request_limit_rejection` |
| Shared budget backend | Local/customer values use Redis-compatible shared counters for multi-replica gateways | Replace bundled Redis with customer managed Redis when available | `test_redis_budget_tracker_shares_usage_across_tracker_instances` |
| Quota and chargeback | `platform/governance/quota-plans.yaml` connects tenant quotas, gateway budgets, workspace sizing, and chargeback labels | Align quota plans to customer showback or chargeback policy before onboarding tenants | `make quota-check`, `make quota-report` |
| Sandbox isolation | `ai-sandbox` namespace, quota, limits, default-deny network policy | Per-team sandbox namespaces with quotas and egress allowlists | `make sandbox-smoke` |
| Tenant labs | `make tenant-up` and `make tenant-smoke` create team namespaces with quota, RBAC, trace contract, and network controls | One namespace per team or approved experiment boundary | `make tenant-smoke` |
| Tenant onboarding | `TenantOnboarding` spec renders tenant controls and matching agent workspace values | Review generated quota, RBAC, PVC, storage, and egress before apply | `make tenant-onboard`, `scripts/tenant-onboard.py --check` |
| Regulated offline tenant profile | `tenants/onboarding/regulated-offline-coding-agents.yaml` renders confidential, no-external-egress agent controls | Use for offline or regulated teams and add egress only through reviewed catalog-backed changes | `make tenant-onboard-regulated`, `make production-check` |
| RAG service | Local retrieval service returns platform context and OpenAI-compatible grounded messages | Replace or extend the knowledge set with customer-approved internal docs | `make rag-smoke`, RAG service tests |
| Vector RAG profile | `deploy/charts/qdrant-vector-store` and customer RAG values provide a persistent Qdrant backend | Size storage, vector dimensions, and ingestion to the customer's embedding model and approved document pipeline | `make production-check`, Qdrant/RAG Helm renders |
| Agent workspaces | `agent-workspace` chart creates a locked-down namespace, PVC, RBAC, trace contract, and approved egress for coding agents | One workspace per team, project, or agent boundary with customer-approved external egress | `make agent-smoke` |
| Egress governance | `platform/network/egress-catalog.yaml` requires external agent egress to reference approved catalog entries | Review and expire Git, package mirror, artifact, and ticketing egress entries | `make egress-check`, `make egress-report` |
| Chaos drills | Safe rollout drills for gateway, budget Redis, Ollama, RAG, Qdrant, vLLM, and GPU capacity preflight | Run after platform upgrades and before customer demos or maintenance windows | `make chaos-drill`, `DRILL=gpu-capacity-preflight RUN_SMOKE=0 make chaos-drill` |
| Evaluation harness | `platform/evals/smoke-suite.yaml`, `platform/evals/coding-agent-suite.yaml`, and `make eval` for repeatable prompt and coding-agent checks | Maintain environment-specific suites and keep summaries as release evidence | `scripts/eval-suite.py --check-config` |
| Release gates | `platform/slo/release-gates.yaml` enforces eval, load, restore, strict toolchain, SLO, governance, supply-chain, and evidence-pack thresholds | Run strict gates before demos, releases, restore reviews, and production-readiness handoff so checked-in sample evidence cannot pass | `make release-gate`, `make release-gate-strict`, `make release-report-strict` |
| Autoscaling | KEDA ScaledObject from Prometheus request rate | Tune thresholds to customer SLOs and GPU capacity | `helm template` in `make validate` |
| Observability | Prometheus metrics, Grafana dashboard, Loki-ready structured logs | Centralize metrics, logs, and alerts | `make validate`, dashboards in `deploy/observability/` |
| Policy as code | Kyverno required labels, resources, pod hardening, read-only root filesystems, image signature audit | Enforce on AI namespaces and exclude platform operators | `make policy-test` when Kyverno CLI is installed |
| Cost controls | Required owner/cost/environment/sandbox labels and OpenCost app | Map labels to chargeback/showback taxonomy | `make validate` YAML checks |
| Secret handling | External Secrets examples and no committed runtime tokens | Replace local Kubernetes provider with enterprise backend | `deploy/clusters/customer/external-secrets.yaml` |
| Supply chain | Pinned Alpine runtime images, hashed Python dependency locks, runtime-only Python dependencies, high/critical Trivy image and repo failure gates, local SBOM/SARIF/checksum evidence, Cosign digest signing, workflow artifacts, and release asset upload in CI | Promote only immutable signed/scanned image digests with downloadable evidence | `make dependency-lock-check`, `make repo-security-scan`, `make image-scan`, `make supply-chain-check`, GitHub Actions image job |
| Backup and restore | `restore-drill` application-data validation and Velero examples | Run scheduled restore evidence for each critical data store | `make restore-drill`, `make backup-drill` |
| Load testing | k6 chat-completion scenario with sandbox tags, live-gateway mode, and self-contained local gateway-path mode | Store summaries and compare against SLOs | `make loadtest`, `make loadtest-local` |
| Evidence pack | Static customer handoff report plus optional live Kubernetes readiness checks | Attach reports to release, demo, restore drill, or incident review evidence | `make evidence`, `make evidence LIVE=1` |

## Promotion Review

Use the matrix above as the source of truth, then review this shorter sequence before a customer handoff or production-style demo:

- Run `make validate-full`, `make api-contract`, `make config-contract`, and `make release-gate-strict` against current evidence.
- Confirm auth, prompt redaction, model allowlists, sandbox budgets, quota labels, and NetworkPolicies match the target environment.
- Review tenant onboarding output before applying it; regulated/offline tenants must keep external CIDR egress disabled.
- Verify RAG knowledge, vector-store dimensions, GPU resource names, runtime replicas, HPA/PDB settings, and topology spread against customer capacity.
- Run smoke, RAG, agent, eval, load, restore, and chaos evidence paths that are relevant to the handoff.
- Confirm image scan, SBOM, checksum, signature, and repo security evidence exists for the images being promoted.
- Generate an Evidence pack and attach the Markdown report to the handoff notes; retain JSON evidence with the release or drill record.
