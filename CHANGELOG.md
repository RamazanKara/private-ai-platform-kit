# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

Client-visible governance: budget headroom on every response, receipts that
prove *when*, a fail-fast SDK, and architecture diagrams that finally show the
hardened runtime.

### Added

- The gateway reports sandbox budget headroom on inference responses with
  OpenAI-style `x-ratelimit-limit-requests` / `x-ratelimit-remaining-requests` /
  `x-ratelimit-limit-tokens` / `x-ratelimit-remaining-tokens` headers — the
  headers agent frameworks and SDK middleware already parse. Each dimension is
  emitted only when a budget limit is configured, remaining counts floor at
  zero, and cache hits (which reserve nothing) omit them. Documented in the
  client examples and the budget-controls runbook.
- Audit receipts now carry a chain-covered `ts` timestamp (Unix epoch seconds)
  inside the tamper-evident hash. Previously the only timestamps lived in the
  log transport, outside the chain, so log access was enough to rewrite *when*
  an action happened without breaking the chain that proves *what* happened;
  the auditor reference's time-window queries now match live gateway events.
  Events recorded before v0.16.0 lack the field (noted in ADR 0006).

### Changed

- The Python SDK fails fast with a typed `GatewayRetryAfterError` (an
  `httpx.HTTPStatusError` subclass carrying `retry_after`) when the gateway
  advertises a `Retry-After` beyond `retry_after_cap` — retrying sooner cannot
  succeed, so the client no longer burns capped sleeps against an exhausted
  budget window. Delays at or under the cap keep the sleep-and-retry behavior.
- The gateway's streaming path no longer JSON-parses every SSE delta chunk
  hunting for the terminal usage object: a `"usage"` substring pre-filter skips
  the parse for the hundreds of delta events per completion that cannot carry
  it.
- The architecture diagrams (all four profiles) and the component table now
  show the hardened agent-sandbox runtime and its controller — the README's
  headline control was previously invisible in every diagram — and the
  local-lab CNI guidance points at the shipped `LOCAL_CNI=calico` path instead
  of hand-rolled kind config.

### Fixed

- The quickstart's "What just happened" recap now matches what
  `make quickstart` actually runs: the agent-sandbox controller install step
  was missing, and `QUICKSTART_DIRECT_APPLY=1` replaces only the Argo
  bootstrap — sync still runs, as a direct Helm apply. The "Keep exploring"
  list gains `make agent-sandbox-smoke` and realigned comments.
- The docs-site landing page now leads with the hardened workspace runtime,
  and the glossary disambiguates the three colliding "sandbox" usages: the
  agent-sandbox workspace runtime, the traceable `ai-sandbox` namespace, and
  `X-Sandbox-ID`.

## v0.15.0 - 2026-07-02

Opinionated de-bloat (ADR 0010): one workspace runtime, one install path per
environment, unambiguous names. Plus a connection-pooling pass on the RAG
request path, a `Retry-After`-aware SDK with its first test suite, and a
hygiene guard against stale `make` targets in the docs.

### Changed

- The hardened agent-sandbox runtime is the standard and only workspace runtime:
  the `sandbox.runtime` toggle is gone, the agent-workspace chart always renders
  the hardened Sandbox, and the controller is a platform prerequisite — a new
  `agent-sandbox-controller` Argo CD Application in both overlays (server-side
  apply, early sync wave) and an install step in quickstart.
- The short-lived projected workspace credential is enabled by default.
- `make agent-smoke` and `make agent-sandbox-smoke` are validation-only: they
  check the GitOps-managed (or explicitly Helm-installed) workspace instead of
  installing a parallel release; `make agent-sandbox-demo` provisions its own
  namespace-scoped instance.
- `make sandbox-smoke` is renamed `make trace-smoke` (the script is renamed
  with it) so "sandbox" unambiguously means the workspace runtime; the new name
  aligns with the `traceable-sandbox` Application that deploys the traced
  `ai-sandbox` namespace. CI e2e also runs `make agent-sandbox-smoke`.
- `C-ISOLATE` is mandated at every risk tier, and live evidence packs fail when
  the agent-sandbox controller is absent instead of recording an unclaimed
  control.
- The RAG service reuses one pooled `httpx.AsyncClient` per component — Qdrant
  retriever, OpenAI-compatible embedding provider, and reranker — instead of
  opening a fresh TCP connection per request (the pattern the gateway's
  `RuntimeClient` already uses), closes the pools on shutdown, and no longer
  tokenizes over-fetched Qdrant candidates whose token counts were never read.

### Added

- `LOCAL_CNI=calico` for the local lab (`scripts/local-up.sh`): creates the
  kind cluster without the default CNI and installs a pinned Calico, so
  default-deny NetworkPolicies and the fail-closed egress smoke are genuinely
  enforced locally instead of advisory (kindnet remains the default).
- The Python SDK honors the gateway's `Retry-After` header when it retries
  429/5xx responses, sleeping the longer of the advertised delay (capped by a
  new `retry_after_cap` argument, default 30 s) and the exponential backoff, so
  clients stop hammering a gateway that told them when to come back. The SDK
  also gains its first test suite, run by `make test-gateway` and
  `make validate`.
- `make repo-hygiene` (and therefore `make validate`) verifies that every
  `make <target>` reference in the docs, runbooks, and the recorded quickstart
  capture names a real Makefile target, so a renamed or removed target can no
  longer leave stale instructions behind.

### Fixed

- Fresh-cluster Argo bootstrap actually converges now: Kyverno, KEDA, and
  Prometheus-operator CRDs exceed the client-side last-applied annotation limit
  (platform-operators and kube-prometheus-stack sync with ServerSideApply);
  ServiceMonitors, ScaledObjects, and the PrometheusRule carry
  `SkipDryRunOnMissingResource` so first-boot syncs no longer deadlock on CRDs
  installed by sibling Applications; the Kyverno cleanup CronJobs pin
  `bitnamilegacy/kubectl` (docker.io/bitnami/kubectl was purged from Docker
  Hub). A long-lived lab never exposed any of this.
- `make agent-sandbox-install` server-side-applies with `--force-conflicts` so
  it stays idempotent next to the Argo-managed controller Application, and the
  demo pins the aider image tag — the platform's own `block-latest-tags`
  policy (correctly) denies tag-less images at admission.
- The traceability runbook and the recorded quickstart output no longer
  instruct the removed `make sandbox-smoke`, and `make help` now lists
  `trace-smoke` and `tenant-smoke`.

### Removed

- `make agent-lab-up` and `scripts/agent-lab-up.sh`: the manual Helm install
  path collided with the Argo-managed `agent-workspace` Application over
  resource ownership. GitOps owns the workspace instance; bare clusters use the
  documented one-line `helm upgrade --install`.

## v0.14.0 - 2026-07-02

A repository-wide review pass (services, deploy tree, docs, CI) fixing drift,
resilience gaps, and controls that shipped but never actually deployed or fired.
Plus: coding-agent workspaces gain an optional hardened runtime on
kubernetes-sigs/agent-sandbox (ADR 0009).

### Added

- Hardened coding-agent workspace runtime on kubernetes-sigs/agent-sandbox v0.5.0
  (ADR 0009): `sandbox.runtime: agent-sandbox` in the agent-workspace chart renders a
  hardened `Sandbox` (non-root, read-only root filesystem, no service-account token,
  optional kernel-isolation runtime class) bound 1:1 to the gateway's `X-Sandbox-ID`;
  vendored, checksummed controller manifests installed via `make agent-sandbox-install`.
- Kyverno `ai-platform-hardened-sandboxes` policy enforcing sandbox identity labels and
  the hardened pod template at admission, with good/bad test resources.
- `make agent-sandbox-smoke`: hardening contract, DNS positive control, and a
  fail-closed probe against a non-catalog destination, with detection of
  NetworkPolicy-incapable CNIs (kindnet) instead of a vacuous pass.
- `make agent-sandbox-demo`: controller install → hardened workspace → blocked
  exfiltration → evidence pack, with the governed model path exercised when the
  gateway is deployed.
- Governance: `C-ISOLATE` control in `platform/governance/control-framework-map.yaml`
  and the AI-governance crosswalk (recommended at `medium` tier, mandated at `high`);
  evidence pack gained static agent-sandbox asset checks and a live controller check;
  threat model documents the agent-workspace isolation boundary.
- Agent-action receipts: gateway audit events now carry `action_type`, `decision`
  (`allowed`/`denied` — covering admission rejects, budget exhaustion, and guardrail
  blocks, with the reason in `error`), and `guardrail_action`, turning the
  tamper-evident chain into a per-action receipt stream for sandbox workloads.
- Short-lived workspace credential (`workspace.credentials.projectedToken`): an
  opt-in projected, audience-bound ServiceAccount token (kubelet-rotated, min 600 s
  TTL) replaces long-lived secrets in agent workspaces; path and audience are
  published in the `agent-platform-contract` ConfigMap, and the gateway verifies it
  via its existing JWT/JWKS auth.
- `make agent-sandbox-demo` now drives a real coding agent (aider) inside the
  hardened sandbox against the governed gateway on the full lab: allowed and denied
  receipts on the audit chain, sandbox-id attribution via model extra headers, and
  honest degradation notes when the local CPU model is too small to complete the
  coding task. The smoke also detects pod drift from Sandbox template changes
  (image/volumes) and refreshes the singleton pod, and adopts pre-existing
  GitOps-managed namespaces instead of fighting Helm ownership.

### Fixed

- Gateway: a Redis outage on the rate-limit path returned 500s (now the budget
  tracker's 503 + Retry-After contract) and a crash between INCR and EXPIRE could
  lock a sandbox out permanently; a response-cache outage failed requests instead
  of degrading to a miss; `/v1/batches` bypassed the output guardrail and skipped
  token/cost metrics and per-item audit; malformed base64 in a JWT signature and a
  non-JSON JWKS body produced 500s; streaming responses escaped
  `MAX_CONCURRENT_REQUESTS`; cache hits double-counted token/cost metrics;
  SSE usage parsing lost events split across network chunks.
- RAG: bootstrapped knowledge points lacked the `classification`/`owner` payload
  fields, so the classification allowlist silently filtered out the whole
  bootstrap corpus.
- SDK: `chat_stream` silently swallowed the gateway's terminal error event
  (now raises `GatewayStreamError`); added the missing `/v1/sandbox/budget`
  accessor.
- Deploy: the alerts PrometheusRule and Grafana dashboard ConfigMaps were
  reachable by no Argo application; qdrant and budget-redis pods violated the
  platform's own Enforce required-labels policy; KEDA/HPA scale-ups were reverted
  by Argo selfHeal (replicas now omitted when autoscaling owns them); the gateway
  NetworkPolicy blocked the Prometheus scrape and KEDA scaler; Ollama/vLLM had no
  NetworkPolicies at all; `OllamaRuntimeAbsent` fired permanently against a scrape
  job that never existed while `VllmRuntimeAbsent` paged intentionally-scaled-to-
  zero labs (both now use kube-state-metrics desired-vs-ready); restore-drill
  metrics pushed to a nonexistent Service name; OpenCost queried a Prometheus this
  stack does not install; the umbrella chart rendered duplicate Namespace objects
  and CRD-dependent extras that broke `helm install` on a bare cluster.
- Versions/docs: `CITATION.cff` was three releases stale; the committed customer
  overlay pinned `v0.11.0`; the Makefile `CUSTOMER_REVISION` default pinned
  `v0.10.0`; client examples requested a model the local allowlist rejects and
  streamed against a profile that disables streaming; runbook site pages had
  404 edit links.

### Changed

- CI: the docs strict build now gates pull requests; a PR-time image build check
  catches Dockerfile/lock regressions before merge; duplicate scan/Scorecard/
  validate work removed; concurrency groups, job timeouts, pip/tool/image-layer
  caching added; CodeQL also analyzes the workflows; the coverage floors are now
  enforced in CI; lint/type targets align with the Python 3.14 runtime images.
- Security scanning: the Trivy repo scan renders Helm charts with the tested
  Kubernetes version (chart misconfig findings were previously skipped silently)
  via a shared `scripts/repo-security-scan.sh`, and skips `deploy/vendor`
  (pinned upstream manifests).
- Gateway: synchronous Redis calls moved off the event loop; sandbox metric-label
  cardinality is bounded; moderations input gets the shared admission size
  ceiling; budget accounting charges multimodal content by extracted text.
- Governance guards: `production-check` now pins the Makefile `CUSTOMER_REVISION`,
  the `CITATION.cff` version, and the kind-vs-Trivy Kubernetes version pair;
  `repo-hygiene` asserts the ruff pin matches between requirements-quality and
  pre-commit; the customer-overlay default revision derives from the CHANGELOG.
- Docs: documented the kind/kindnet NetworkPolicy non-enforcement limitation, the
  RAG tenant-isolation trust boundary, and the customer Ollama model-preload
  prerequisite.

### CI (post-v0.13.0, previously unlisted)

- Vendor umbrella chart dependencies before packaging so the released `platform`
  chart tarball is installable from OCI.

## v0.13.0 - 2026-07-01

A reference-architecture hardening pass closing a second audit of the gateway, RAG, serving,
observability, security, resilience, governance, and documentation surfaces.

### Added

- Gateway: response-path **output guardrail** (`OUTPUT_GUARDRAIL_*`) that inspects completions for
  leaked credentials/PII/blocked content and flags, redacts, or blocks them before return/caching
  (OWASP LLM02/LLM06); an `inference_gateway_output_guardrail_total` metric and `X-Output-Guardrail`
  header; streaming detect-and-flag.
- Gateway: optional **shared Redis response cache** (`RESPONSE_CACHE_BACKEND=redis`) so the cache
  hit rate does not collapse under horizontal scale-out; an `inference_gateway_estimated_cost_usd_total`
  Prometheus cost metric; graceful `503` on budget-backend outage (was an unhandled `500`).
- RAG: **per-tenant retrieval isolation** (`retrieval.tenantIsolation`) scoping results to the
  caller's tenant via the ingest-stamped `owner` field; a pluggable **cross-encoder reranker**
  (`retrieval.reranker`, OpenAI/TEI/Cohere-compatible) second stage.
- Evals: **RAGAS-style** context-precision and answer-faithfulness scoring in `scripts/rag-eval.py`
  (`retrieval_hit_rate` renames the misnamed `grounding_rate`); an adversarial **safety/jailbreak/
  bias** suite (`platform/evals/safety-suite.yaml`) gated by a new `safety` release gate; a PR-time
  `make eval-local` gate in CI.
- Serving: first-class vLLM knobs (`server.enablePrefixCaching`, `gpuMemoryUtilization`,
  `kvCacheDtype`, `quantization`, `guidedDecodingBackend`, speculative) plus FP8/AWQ quantization
  cluster profiles and quantization/speculative/MIG guidance.
- Observability: **Tempo** trace backend + Grafana datasource (OTLP spans now have a destination);
  cost/FinOps dashboard panels and an OpenCost allocation dashboard; a real Alertmanager
  critical-severity receiver.
- Security: apiserver-native **Pod Security Admission** (restricted) on the platform data-plane
  namespaces; an opt-in **encryption-in-transit** overlay (mesh mTLS / cert-manager); an opt-in
  **runtime threat detection** (Falco) Argo app + runbook.
- Governance: per-model **model cards/datasheets** (checked by `make model-check`), an
  **OWASP LLM Top 10** control mapping, an **AI governance crosswalk** (NIST AI RMF / EU AI Act /
  ISO 42001) with a machine-readable `control-framework-map.yaml`, and model **drift-monitoring**
  alerts + runbook.
- Resilience & reference docs: a **disaster-recovery** runbook (RPO/RTO + restore order), a
  **failure-mode / graceful-degradation** matrix, a **capacity-sizing** worksheet, **ADRs**,
  per-profile **architecture** diagrams, a **cost model/TCO**, and a **scope/non-goals** doc.
- Distribution & DX: an **umbrella Helm chart** for whole-stack install, `values.schema.json` on
  the remaining charts, a retry-aware/streaming/packaged first-party **SDK**, and OpenAI-client
  drop-in examples for agent/coding frameworks.

## v0.12.0 - 2026-06-30

A feature-gap remediation pass closing the highest-impact items from an audit of the
gateway, RAG, deployment, and governance surfaces.

### Added

- Gateway: tool/function-calling and OpenAI passthrough params now survive to the runtime
  (the schema previously dropped them), and `Message.content` accepts content-part arrays
  for vision-capable runtimes. Tool count/size are bounded by admission.
- Gateway: governed `POST /v1/embeddings` (auth/policy/budget/audit) and a dependency-free
  OpenAI-compatible `POST /v1/moderations` (credential/PII/blocked-term classification).
- Gateway: short-window per-sandbox rate limiting (Redis or in-memory), cross-runtime
  fallback/failover routing, and the authenticated principal is now propagated into the
  audit trail with optional JWT-claim-bound sandbox identity.
- Gateway: opt-in PII detectors (email/us_ssn/credit_card) and a blocked-term denylist.
- RAG: retrieval-quality evaluation (`make rag-eval`) scoring recall@k/MRR/nDCG/grounding
  against a golden set, and delete-by-source ingestion (`--delete --source-id`) for
  right-to-erasure.
- vLLM: optional persistent model-weight cache (PVC) and `model.revision` pinning.
- Observability: a Promtail log-shipper Application feeding Loki, GPU-saturation and
  request-queue alerts, SLI recording rules, and a default Alertmanager routing tree.
- Gateway: bounded-concurrency load shedding (503), an exact-match per-sandbox response
  cache, and a per-process tamper-evident audit hash chain (prev_hash + record_hash,
  verifiable by the paper's auditor tooling).
- RAG: hybrid dense+lexical retrieval with rerank and classification-scoped retrieval
  access control.
- Deployment/DX: an optional gateway Ingress (host + TLS), a tenant offboarding/
  deprovisioning plan generator (scripts/tenant-offboard.py), and a client API examples
  doc (curl / openai SDK / httpx).
- Governance: the RAG embedding model is now governed in the model catalog and promoted to
  approved with a model-provenance entry and promotion request (chat/embeddings parity).
- Gateway: weighted canary / A-B and shadow model routing (progressive delivery), a
  synchronous batch endpoint (POST /v1/batches), and a usage+cost endpoint (GET /v1/usage)
  with a token-to-cost model.
- RAG: age-based retention purge (ingestion timestamp + a --purge --older-than-days mode).
- Security/policy: a Kyverno encryption-at-rest attestation policy for platform PVCs.
- DX/tenancy: a first-party Python client SDK (sdk/), tenant-onboard --apply (self-service),
  and multi-node distributed-serving guidance (LeaderWorkerSet) in the GPU-capacity runbook.

### Changed

- Gateway runtime retries now cover transient 5xx/429 (and pre-first-byte streaming) with
  exponential backoff + jitter and `Retry-After`; default retries raised from 0 to 2.
- The default customer vLLM profile uses KEDA queue-scaling instead of a CPU-target HPA
  (the wrong signal for a GPU server); `production-check` now renders and gates it.

### Fixed

- Version strings aligned to v0.11.0 so `make production-check` passes; several other
  pre-existing offline `make validate` breakages (paper/ lint scope, mkdocs YAML parse,
  undeclared `paper/` directory) resolved.

## v0.11.0 - 2026-06-30

### Changed

- The inference gateway now reuses a single pooled upstream HTTP client (keep-alive plus `TCP_NODELAY`) instead of constructing a new `httpx.AsyncClient` per request. Reusing the client keeps the connection pool warm and removes per-request client and TLS-context setup from the hot path, which substantially raises single-worker throughput and lowers per-request latency; the shared client is closed on gateway shutdown.

### Added

- A `paper/` reproducibility artifact for the companion paper *Auditable Private LLM Serving on Kubernetes*: the cost-of-compliance benchmark and governance microbenchmark, the conformance suite, the tamper-evident audit-chain tooling, an external-baseline (LiteLLM) runner, and a `PAPER.md` mapping each claim to the command that regenerates it.
- `.zenodo.json` so GitHub releases archived by Zenodo carry correct metadata (author ORCID, license, and the supplement link to the paper), and a Zenodo DOI badge in the README.

### Note

- The `v1.0.0-paper` and `v1.1.0-paper` tags are GitHub pre-releases that snapshot the exact revisions cited by the paper; they are archived to Zenodo for citation and sit outside this normal release line.

## v0.10.0 - 2026-06-29

A documentation and repository-structure release. No runtime, chart, or API behavior changes — the deployed artifacts are functionally identical to `v0.9.0`; only the documentation, the repository layout, and the version string changed. Adopters who reference repository paths directly (rather than through the pinned Argo CD applications) must update them — see the migration note below.

### Added

- A documentation site built with mkdocs-material in the [Diátaxis](https://diataxis.fr/) structure (Tutorials / How-to / Reference / Explanation), deployed to GitHub Pages by a SHA-pinned workflow. A new learning-oriented "Your first private AI platform" tutorial. The build runs `mkdocs --strict`, so a broken link or nav drift fails the deploy. Set repo Settings → Pages → Source = "GitHub Actions" to publish.
- `scripts/paths.py` and `scripts/_paths.sh`: a central registry that is the single source of truth for the repository directory layout, with `--dump` (JSON manifest), `--dump-sh` (shell variables), and `--check` (a drift guard that flags any undeclared top-level directory). `repo-hygiene` now derives its directory inventory from it, closing a gap that omitted `chaos/`, `config-contracts/`, and `rag/`. New `make paths` / `make paths-check` targets.

### Changed

- Repository restructure for a curated top level. `services/` → `src/`; charts, clusters, gitops, policies, sandbox, backup, and observability → `deploy/`; governance, network, slo, model-catalog, evals, rag, api-contracts, config-contracts, and tools → `platform/`; `tests/load` → `loadtest/`. Argo CD `source.path`s, Helm `valueFiles`, CI path filters, the Makefile, and every governance gate were repointed in lockstep; `git mv` preserves file history. Verified end to end with `make validate` (helm render, kubeconform 0-invalid, kyverno, and the full gate suite).
- The 11 documents were rewritten/normalized for Diátaxis voice, and cross-area links that leave the doc set now use absolute GitHub URLs so both `repo-hygiene` and `mkdocs --strict` pass.
- Generated output is consolidated under a git-ignored `.out/` (rendered tenant artifacts in `.out/tenants/`); the evidence tree stays in a visible top-level `results/` with sample evidence tracked and generated reports git-ignored.

### Removed

- The hand-rolled `docs/index.html`, replaced by the documentation-site landing page.

### Migration

- Repository paths changed: `charts/<x>` → `deploy/charts/<x>`, `clusters/` → `deploy/clusters/`, `gitops/` → `deploy/gitops/`, `policies/` → `deploy/policies/`, `services/` → `src/`, and the governance/contract inputs → `platform/`. Customers deploying through the pinned Argo CD applications (`make customer-overlay`, `CUSTOMER_REVISION=v0.10.0`) need no manual change — the application `source.path`s already point at the new locations. Forks or automation that reference the old paths directly must update them.

## v0.9.0 - 2026-06-29

A platform-hardening release that closes the gap between "claims rigor" and "demonstrably rigorous": controls that enforce rather than appear, a reproducible serving benchmark, and end-to-end supply-chain and operations polish.

### Added

- Reproducible serving benchmark: `scripts/benchmark-ollama.sh` and `make benchmark-local`, plus a documented reference table in `docs/benchmarks-and-evals.md` (qwen2.5:0.5b sustains ~55 tokens/s at p50 0.53s / p95 0.56s on a Ryzen 7 5800X3D, CPU only).
- vLLM/GPU Grafana dashboard (DCGM GPU metrics + vLLM serving metrics) and critical runtime-backend-down alerts; a `runbook_url` annotation on every alert.
- `make model-provenance-verify`, which fetches each model-artifact digest from its source registry and asserts it reproduces (qwen2.5:0.5b and qwen3.5:0.8b verified).
- Optional image digest pinning in every chart, with the third-party images (Qdrant, vLLM, Ollama, Redis, busybox) pinned by their multi-arch manifest-list digest; multi-arch (amd64+arm64) first-party images with OCI labels.
- `.github/dependabot.yml` (github-actions, pip, docker) to keep the new commit-SHA action pins fresh.
- `values.schema.json` for the gateway, RAG, vLLM, and Qdrant charts; `kubeVersion`, `maintainers`, `sources`, and `home` metadata on every `Chart.yaml`.
- `/readyz` endpoints on the gateway and RAG service; optional Redis AUTH for the budget store.
- An Argo CD `AppProject` that locks the customer GitOps source repo and in-cluster destination; an admission-time Kyverno policy denying broad egress CIDRs plus a render-time `catalogRef` requirement for agent-workspace egress.
- Operations runbooks: an upgrade/rollback runbook, an incident-response index (severity tiers + escalation), and a `runbooks/README.md` index; AI-specific threats (indirect/RAG prompt injection, model-weight tampering), a build-pipeline trust boundary, and a data-residency note in the threat model.
- A real Qdrant seed/snapshot/restore data-recovery drill and a true fault-injection drill (scale Qdrant to 0, assert graceful RAG degradation).
- OSS-health files: `NOTICE`, `.github/SUPPORT.md`, a Code of Conduct reporting contact, a GPU sizing table, and a fair named-alternatives comparison (LiteLLM, BentoML/OpenLLM, KServe, KubeAI).
- A real security disclosure channel (GitHub Private Vulnerability Reporting + `security@fluentorbit.de`, with an acknowledgement SLA and coordinated-disclosure policy) and visible fluentorbit stewardship (`GOVERNANCE.md`, `MAINTAINERS.md`, `.github/FUNDING.yml`).
- Proposed model-catalog entries for newer self-hostable vLLM models (`Qwen/Qwen3.6-35B-A3B`, `zai-org/GLM-5.2`, `deepseek-ai/DeepSeek-V4-Flash`); OpenSSF Scorecard triage, Qdrant migration, and tenant-example runbooks; per-chart install-profile sections.

### Changed

- Default local Ollama smoke model is now `qwen2.5:0.5b` (fast, non-reasoning) so the laptop CPU quickstart completes in seconds; the larger `qwen3.5:0.8b` reasoning model is the customer Ollama default. Both carry reproducible Ollama-registry model-layer provenance digests and promotion requests. The customer coding-agent default `Qwen/Qwen3-Coder-Next` is unchanged.
- Kyverno image-signature verification flipped from Audit to **Enforce** (it now gates the published, signed images at admission; local-built and third-party images are unaffected).
- Velero backups now protect the data-bearing namespaces (Qdrant, agent workspaces, Argo CD) and capture PVC contents, with backup-failure and staleness alerts; the customer overlay documents the backup prerequisite.
- The model-promotion gate enforces eval-model match (or a documented, justified proxy) and separation of duties; provenance requires a pinned source revision; `production-check` is decoupled from specific model IDs so customers can swap models without editing the gate.
- All GitHub Actions pinned to commit SHAs; Trivy image scans broadened to vuln + secret + misconfig; Helm chart OCI artifacts are now cosign-signed.
- Blocking `httpx` calls in async handlers converted to `httpx.AsyncClient` (RAG retriever/embeddings, gateway JWKS, with last-known-good caching and 503-vs-401 distinction); streaming requests now record metrics/usage/audit at end-of-stream and surface mid-stream/pre-first-byte upstream errors as 502; readiness probes point at `/readyz`.
- vLLM autoscales on queue depth via a KEDA ScaledObject (not CPU), runs as an explicit non-root user, and has startup + liveness probes; the AMD profile pins the correct ROCm image digest.
- The customer GitOps overlay is pinned to an immutable tag (the configurator rejects HEAD/branch); the Argo CD bootstrap manifest is pinned to a release tag; agent-workspace RBAC is split so the viewer group is strictly read-only.

### Fixed

- The Argo CD quickstart path now works non-interactively: `scripts/sync.sh` runs the CLI in `--core` mode (no login needed) and waits for the smoke-critical runtime workloads to roll out before returning, so the smoke no longer races the reconcile.
- The `rag-service` NetworkPolicy denied all egress (including DNS) in the default lexical mode; DNS is now always allowed and vector-store/embedding egress is conditional. Qdrant now allows its exposed gRPC ingress port.
- `evidence-pack.py` referenced a long-renamed promotion request, which had silently broken `make validate`.
- Documentation accuracy: removed a vendor-tool reference, corrected the Ollama model-library links, aligned the Python version to 3.12+, and synced the ROADMAP coverage figures with the enforced floors.

### Removed

- Dropped the mutable `:latest`/`:main` published image tags (banned by the platform's own Kyverno block-latest policy); removed the superseded `qwen3:0.6b` model.

## v0.8.0 - 2026-06-28

### Added

- Added optional OpenTelemetry distributed tracing to the gateway and RAG service: when `OTEL_TRACING_ENABLED` is set, each request emits a SERVER span linked to the inbound W3C `traceparent` and exports it over OTLP/HTTP to `OTEL_EXPORTER_OTLP_ENDPOINT`. Tracing is disabled by default and configured via the `observability.tracing.*` chart values.
- Added Grafana dashboard provisioning: `make dashboard-update` renders sidecar-loadable ConfigMaps (labelled `grafana_dashboard: "1"`) from the canonical dashboard JSON, and `make dashboard-check` fails when the generated ConfigMaps drift from their sources.
- Added an OIDC/JWKS rotation runbook with IdP-specific endpoint examples (Keycloak, Auth0, Okta, Microsoft Entra ID) and a key-rotation drill.

### Changed

- Raised the enforced `make coverage` floors to 85% (gateway) and 84% (RAG) on the back of new JWT claim-validation and JWKS-rotation tests, runtime-client streaming/health tests, a RAG ingestion CLI test suite, and tracing tests.

### Validation

- `make validate-full`
- `make quality`
- `make coverage`
- `make image-scan`

## v0.7.0 - 2026-06-28

### Added

- Added `helm test` connection probes for the inference-gateway and rag-service charts (`tests.enabled`), rendered for chart validation and run on demand with `helm test`.
- Added a per-service Grafana dashboard for the RAG service and a `make dashboard-check` gate (wired into `make validate`) that fails when a dashboard references a metric the services do not emit.
- Added public-API docstrings across both service codebases.
- Added gateway runtime-client streaming, health-fallback, and circuit-breaker tests plus a RAG ingestion test suite, and raised the enforced `make coverage` floors to 84% (gateway) and 78% (RAG).
- Added a `CITATION.cff` for repository citation metadata.

### Validation

- `make validate-full`
- `make quality`
- `make coverage`
- `make dashboard-check`

## v0.6.0 - 2026-06-28

### Added

- Added an enforced Python code-quality gate: Ruff lint, Ruff format check, and mypy type checks for both services, wired into `make validate` / `make validate-full` and exposed as `make quality`, `make lint`, `make typecheck`, `make format`, and `make coverage`. Tooling is hash-pinned in `requirements-quality.lock` and runs from an isolated `.venv-quality`, leaving the runtime and dev locks untouched.
- Added a `CodeQL` workflow for Python static analysis (SAST) on pushes, pull requests, and a weekly schedule.
- Added an optional `pre-commit` configuration mirroring the quality gate.

### Fixed

- Fixed the inference gateway recording the resolved `ModelRoute` object instead of the request path in the `route` label of the `inference_gateway_requests_total` and `inference_gateway_request_duration_seconds` metrics after a successful chat completion (surfaced by the new mypy gate). Added a regression test.

### Validation

- `make quality`
- `make validate-full`

## v0.5.0 - 2026-06-27

Feature-completeness work for gateway policy, RAG ingestion, chart documentation, and public verification.

### Added

- Added optional gateway JWT/JWKS bearer-token validation for HS256, RS256, and ES256, `GET /readyz`, `GET /v1/models`, YAML-backed `ModelRoutingPolicy`, YAML-backed `SandboxPolicySet`, and bounded runtime retry/circuit-breaker controls.
- Added RAG embedding providers for deterministic local hash vectors and customer-owned OpenAI-compatible embedding endpoints.
- Added RAG source metadata manifests, local `scripts/rag-ingest.py`, and an optional RAG chart ingestion Job for Qdrant upserts with classification, retention, owner, and embedding metadata.
- Added generated Helm chart README value tables for all charts and `make chart-docs`.
- Added release verification docs for Helm OCI charts, Cosign image signatures, SBOM checksums, Trivy SARIF, and strict evidence.

### Changed

- RAG health output now reports source-manifest configuration and richer Qdrant vector metadata.
- Gateway audit events now record the routed runtime backend instead of only the default backend.

### Validation

- `make validate-full`
- `make eval-local`
- `make loadtest-local`
- `make image-scan`
- `make supply-chain-check`
- `make restore-drill`
- `make evidence LIVE=1`
- `make release-gate-strict`
- GitHub Actions manual proof run: `validate`, `scheduled-proof`, and `local-e2e`

## v0.4.2 - 2026-06-24

Dependency and image security follow-up for the private AI platform kit.

### Changed

- Updated Helm chart versions to `0.4.2` and gateway/RAG chart image defaults to `v0.4.2`.
- Updated customer overlay examples to pin `CUSTOMER_REVISION=v0.4.2`.
- Bumped gateway and RAG runtime dependencies to FastAPI `0.138.0` and Starlette `1.3.1`.
- Repinned the Python Alpine base image to the current `python:3.14-alpine` digest.

### Fixed

- Cleared the Trivy HIGH findings from the `v0.4.1` image release scan for Starlette and Alpine OpenSSL.

### Validation

- `make validate`
- `trivy image --scanners vuln --severity HIGH,CRITICAL`

## v0.4.1 - 2026-06-24

Validation and repository bloat cleanup for the private AI platform kit.

### Changed

- Updated Helm chart versions to `0.4.1` and gateway/RAG chart image defaults to `v0.4.1`.
- Updated customer overlay examples to pin `CUSTOMER_REVISION=v0.4.1`.
- Reduced duplicate production validation by keeping `production-check.py` focused on production/static assertions and leaving script orchestration to `validate.sh`.
- Replaced brittle prose-token checks with policy and implementation checks in production, evidence-pack, quota, and retention validation.
- Collapsed generated evidence ignore rules and retained sample evidence onto a single `results/**/sample-*` convention.
- Trimmed repetitive production-readiness and landing-page copy.

### Validation

- `make validate`

## v0.4.0 - 2026-06-02

Release-gate, supply-chain, and customer-readiness hardening for the private AI platform kit.

### Added

- Added local supply-chain evidence generation with Syft SBOMs, Trivy HIGH/CRITICAL SARIF scans, checksums, summaries, and strict evidence validation.
- Added a local gateway load-test harness backed by an OpenAI-compatible mock runtime so strict release gates can verify current latency and error-rate evidence without a production dependency.
- Added Dependabot policy for GitHub Actions, Docker, and Python dependency updates.

### Changed

- Updated Helm chart versions to `0.4.0` and gateway/RAG chart image defaults to `v0.4.0`.
- Updated customer overlay examples to pin `CUSTOMER_REVISION=v0.4.0`.
- Tightened release gates to require supply-chain evidence and complete load-test metrics instead of accepting missing latency data.
- Hardened validation tooling so managed local tools, script executable modes, bytecode suppression, and dependency update policy are checked consistently.

### Fixed

- Sanitized gateway backend failure responses so runtime URLs and secret-bearing snippets are not exposed through 502 errors.
- Rejected whitespace-only RAG queries and explicit zero values for RAG retrieval limits.

### Validation

- `make image-scan`
- `make supply-chain-check`
- `make loadtest-local`
- `make release-gate-strict`
- `make release-report-strict`
- `make production-check`
- `make repo-hygiene`
- `make validate-full`

## v0.3.2 - 2026-06-01

Release, security, and customer-handoff hardening for the private AI platform kit.

### Added

- Added strict API contract snapshots for the gateway and RAG service, with validation for public routes, request schemas, operation IDs, and auth declarations.
- Added runtime configuration contract snapshots for gateway and RAG env vars, Helm env mappings, chart defaults, aliases, and secret-sourced API key hashes.
- Added repository hygiene checks, contributor guidance, security policy, CODEOWNERS, and Makefile help output.
- Added local `make image-scan` coverage for gateway and RAG runtime images.

### Changed

- Updated Helm chart versions to `0.3.2` and gateway/RAG chart image defaults to `v0.3.2`.
- Updated customer overlay examples to pin `CUSTOMER_REVISION=v0.3.2`.
- Switched gateway and RAG runtime images to a pinned Alpine Python base and split runtime dependencies from test-only dependencies.
- Hardened CI release evidence with high/critical Trivy failure gates, immutable digest signing, checksum artifacts, and release asset upload.
- Made strict release gates require current, non-sample evidence with freshness limits.

### Validation

- `make api-contract`
- `make config-contract`
- `make production-check`
- `make validate`
- `make image-scan`

## v0.3.1 - 2026-06-01

Customer demo readiness fixes for the local lab and GitOps handoff path.

### Changed

- Updated Helm chart versions to `0.3.1` and gateway/RAG chart image defaults to `v0.3.1`.
- Updated customer overlay examples to pin `CUSTOMER_REVISION=v0.3.1`.
- Scoped Argo CD root applications to `apps.yaml` so the app-of-apps path does not try to apply local kind config or values files.
- Made `LOCAL_DIRECT_APPLY=1 make sync` work without requiring an existing Argo CD root application.
- Tuned Qwen3 eval suites to request direct responses with enough token budget for reasoning-model behavior.

### Fixed

- Added a writable Qdrant snapshots mount so the local vector-store profile starts under the non-root security context.
- Redacted runtime `reasoning`, `reasoning_content`, and `thinking` fields from gateway responses.
- Extended prompt secret detection to reject unquoted API-key assignments such as `API_KEY=...` before prompts reach the runtime.

### Validation

- `make validate`
- `make release-gate`
- Live evidence pack: 38 controls passed, 0 failed.
- GitHub Actions CI passed on `main` before this release prep.

## v0.3.0 - 2026-06-01

Customer-ready release packaging and documentation cleanup.

### Changed

- Added a GitHub Pages landing page and moved detailed local commands into `docs/getting-started.md`.
- Reworked the customer-owned Kubernetes README into a deployment checklist with GitOps, secrets, GPU scheduling, values review, smoke test, and handoff steps.
- Added `make customer-overlay` and `make customer-overlay-check` to configure and validate customer fork/mirror repo URLs, target revisions, and NVIDIA/AMD vLLM profile selection.
- Updated Helm chart versions to `0.3.0` and gateway/RAG chart image defaults to `v0.3.0`.
- Updated CI image publishing so branch builds push `:main`, tag builds push `:<tag>` and `:latest`, and every published tag is signed with Cosign.
- Removed timestamped generated reports from the tracked tree; sample evidence artifacts remain.

### Validation

- `make validate`
- GitHub Actions CI passed on `main` for validation, image build, SBOM, Trivy SARIF upload, and Cosign signing.

## v0.2.0 - 2026-06-01

Version refresh and rename release for Private AI Platform Kit.

### Changed

- Renamed the public project and repository presentation to Private AI Platform Kit.
- Updated the README demo media to use the current project name and embedded terminal demo.
- Refreshed runtime, CI, and validation pins, including Ollama `0.24.0`, vLLM `v0.22.0`, Python `3.14`, Redis `8.0`, and current GitHub Actions.
- Replaced the old default model set with `qwen3:0.6b` for local Ollama smoke tests and `Qwen/Qwen3-Coder-Next` for customer vLLM coding-agent profiles.
- Updated model catalog, promotion requests, provenance, eval suites, gateway allowlists, and GPU capacity notes for the new model profiles.
- Installed and verified the optional local validation CLIs, then fixed GitHub Actions release-asset download fallback for strict CI validation.

### Validation

- `make production-check`
- `PATH="$PWD/.tools/bin:$PATH" make validate-full`
- GitHub Actions CI passed on `main` for validation, image build, SBOM, Trivy SARIF upload, and Cosign signing.

## v0.1.0 - 2026-05-31

First public release of Private AI Platform Kit.

### Included

- Local `kind` lab with Argo CD sync path, Ollama runtime, inference gateway, RAG service, agent workspace chart, and sandbox controls.
- Provider-neutral customer overlays for Kubernetes clusters with CPU, NVIDIA GPU, and AMD ROCm GPU runtime profiles.
- OpenAI-compatible inference gateway with API-key auth, trace headers, model allowlists, admission controls, prompt secret detection, metrics, and Redis-compatible sandbox budgets.
- Coding-agent workspaces with PVC storage, namespace RBAC, default-deny networking, approved egress, and RAG access.
- Lexical local RAG and optional Qdrant vector-store profile for customer knowledge bases.
- Model catalog, promotion requests, model provenance, quota and chargeback policy, data retention policy, egress governance, SLOs, release gates, and customer evidence packs.
- Restore verification with restore-drill, Velero-style examples, chaos drills, load tests, evaluation suites, SBOM/signing/scanning workflows, Kyverno policies, and production readiness checks.
- README live demo video generated from a real repository command run.

### Validation

- `make production-check`
- `scripts/evidence-pack.py --check`
- `scripts/release-gate.py --check`
