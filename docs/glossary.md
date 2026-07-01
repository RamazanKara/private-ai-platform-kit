# Glossary

Terms used across the Private AI Platform Kit docs, runbooks, charts, and source. Each definition
describes how *this* repo uses the term, not the concept in the abstract, and links the authoritative
document, runbook, or file where one exists. Entries are alphabetized.

## A

**Admission control** — Gateway-side request validation applied before a request reaches Ollama or
vLLM. The `admission` block in
[`deploy/charts/inference-gateway/values.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/charts/inference-gateway/values.yaml)
caps message count (`maxMessages`), prompt size (`maxPromptChars`), completion tokens
(`maxCompletionTokens`), temperature range, and streaming (`allowStreaming`), plus the prompt
secret-detection policy. Rejections return HTTP 400 and are counted by
`inference_gateway_admission_rejections_total`. See the
[Guardrails](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/guardrails.md)
and [Traceability sandbox](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/traceability-sandbox.md)
runbooks.

## B

**Budget window / estimated-token budget** — The rolling time window (`windowSeconds`, default 86400)
over which a sandbox's usage is accumulated, and the estimated-token ceiling enforced within it.
Estimated tokens are computed as `ceil(prompt chars / estimatedCharsPerToken) + requested max_tokens`
(falling back to the admission completion-token ceiling when `max_tokens` is omitted). An overage
returns HTTP 400 with `sandbox_token_budget_exceeded`. See
[Budget controls](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/budget-controls.md).

**Burn-rate alert** — A Prometheus alert that fires when the inference gateway consumes its
availability error budget too fast. `InferenceGatewayErrorBudgetFastBurn` and
`...SlowBurn` in
[`deploy/observability/alerts/ai-platform-alerts.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/observability/alerts/ai-platform-alerts.yaml)
watch the 99.5 percent objective on short and long horizons. See
[SLO and error budget](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/slo-error-budget.md).

## C

**Canary vs shadow (progressive delivery)** — Two gateway routing behaviors used to roll out a model
or backend change safely. *Canary* sends a weighted fraction of live traffic to a candidate route and
counts it with `inference_gateway_canary_routed_total`. *Shadow* mirrors requests fire-and-forget to a
candidate route without returning its response to the caller
(`inference_gateway_shadow_requests_total`). Both are first-class in the gateway; see
[ADR 0005](adr/0005-openai-compatible-gateway.md) and [Architecture](architecture.md).

**catalogRef** — The field that ties a requested egress destination back to an approved entry in the
egress catalog. A tenant or workspace values file references an approved
`platform/network/egress-catalog.yaml` entry by `catalogRef` (alongside `cidr` and `ports`); the
validator rejects any external destination without a matching, non-expired catalog entry. See
[Egress governance](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/egress-governance.md).

**Context precision (RAGAS-style)** — See *faithfulness / context precision*.

**Cost-center** — One of the required attribution labels (`platform.ai/cost-center`) stamped on
namespaces, pods, and tenant specs so Prometheus, OpenCost-style reporting, logs, and evidence packs
attribute usage to the same owner. Kept stable across upgrades so cost history stays comparable. See
[Quota and chargeback](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/quota-chargeback.md).

**Cross-encoder reranker** — An optional second retrieval stage that reorders over-fetched candidates
by joint query-document relevance, raising precision where dense retrieval is weakest (paraphrase,
synonymy, multi-hop). Configured via `retrieval.reranker` in the RAG chart; the default provider is
`none`, and an `openai-compatible` provider calls a Cohere/Jina/TEI-style `/rerank` endpoint. A
reranker outage is non-fatal — the first-stage hybrid ranking is kept. See
[`src/rag-service/app/reranker.py`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/src/rag-service/app/reranker.py).

## D

**dataClassification** — A model catalog field recording the sensitivity of the data a model is
approved to handle. Validated against `{public, internal, confidential, restricted}` in
[`scripts/model-catalog.py`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/scripts/model-catalog.py)
and must match the model's provenance record. Distinct from *riskTier* (see below): dataClassification
answers "how sensitive is the data?", riskTier answers "how much harm if the model misbehaves?".

**Digest** — See *model provenance / immutableRef / digest*.

## E

**Egress catalog** — The single reviewed allowlist of external network destinations, at
`platform/network/egress-catalog.yaml`. Agent and tenant namespaces are default-deny; any external
CIDR must be an `approved`, non-expired catalog entry (owner, environments, expiry, use cases, data
classification, CIDRs, ports) referenced from values by `catalogRef`. See
[Egress governance](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/egress-governance.md).

**Error budget / SLO** — An SLO (service-level objective) is a target defined in
`platform/slo/objectives.yaml` — gateway error rate, p95/p99 latency, eval pass rate, restore-drill
pass rate, and coding-agent readiness. The error budget is the allowed amount of SLO violation before
burn-rate alerts fire; a failed objective is treated as a customer-readiness blocker. See
[SLO and error budget](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/slo-error-budget.md).

**Evidence pack** — The bundle of static readiness controls plus pointers to the latest generated
operational artifacts, produced by `make evidence` (add `LIVE=1` for live Kubernetes checks). Reports
are written under `results/evidence/`; the Markdown report is the customer-facing summary and the JSON
report feeds automation and audit. See
[Evidence pack](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/evidence-pack.md).

## F

**Faithfulness / context precision (RAGAS-style)** — Deterministic, offline generation-grounding
proxies scored by the RAG eval only on cases that ship a ground-truth `answer`. *Context precision*
measures how much of what retrieval returned was on-topic; *faithfulness* measures how much of the
expected answer is supported by the retrieved context. They are floored by `minContextPrecision` and
`minFaithfulness` in
[`platform/evals/rag-retrieval-suite.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/platform/evals/rag-retrieval-suite.yaml)
and computed in
[`scripts/rag-eval.py`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/scripts/rag-eval.py).

## H

**Hybrid retrieval** — A recall-oriented first-stage ranking that combines dense (embedding) similarity
with lexical term overlap. In the Qdrant profile, candidates are over-fetched and reordered by a hybrid
score; `retrieval.lexicalWeight` controls the blend (`0` reproduces pure dense ranking). Feeds the
optional cross-encoder reranker. See
[`src/rag-service/app/retriever.py`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/src/rag-service/app/retriever.py)
and the [Vector RAG](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/vector-rag.md)
runbook.

## I

**immutableRef** — See *model provenance / immutableRef / digest*.

## M

**MIG (Multi-Instance GPU)** — NVIDIA's hardware partitioning of one A100/H100 into isolated GPU
instances, configured through the NVIDIA GPU Operator. To use it, request the MIG resource name (e.g.
`nvidia.com/mig-1g.10gb`) in `accelerator.resourceName` with `accelerator.count: 1`. It is the
memory-isolated way to share a GPU across tenants (unlike time-slicing). The device-plugin/operator
config is operator-owned. See
[GPU capacity](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/gpu-capacity.md).

**Model card** — The human-readable companion to a model's machine-checked catalog entry and
provenance record. Every `status: approved` model must have a card under
[`platform/model-catalog/model-cards/`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/platform/model-catalog/model-cards/README.md);
cards introduce no new facts — every field is copied from the governed YAML, and `make model-check`
fails if an approved model is missing one.

**Model provenance / immutableRef / digest** — The attested origin of a served model artifact, recorded
in `platform/governance/model-provenance.yaml`. Each approved model carries a `sourceUri`, an
`immutableRef` (an immutable reference pinned by a SHA-256 `digest`), a digest scope and verification
command, and license/risk/classification metadata matching the catalog. The bundled lab uses
source-reference digests; customer production replaces them with real registry/object-store artifact
digests, and the vLLM `model.revision` should be pinned to the attested `immutableRef` so the runtime
artifact cannot drift. See
[Model provenance](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/model-provenance.md).

## O

**Output guardrail** — A response-path control that inspects the model's completion before it is
returned or cached, closing OWASP LLM02 (insecure output handling) and LLM06 (sensitive information
disclosure) — the input-side prompt secret detection cannot catch a secret the *model* emits. Modes are
`flag`, `redact` (default), and `block`; streaming responses are detected/flagged only. Configured
under `guardrails.outputGuardrail`. See
[Guardrails](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/guardrails.md).

## P

**Per-tenant RAG isolation** — A retrieval scope filter that limits a caller to points stamped with
their tenant. When `retrieval.tenantIsolation.enabled` is set, the Qdrant query filter appends a match
on the configured `retrieval.tenantIsolation.field` (default `owner`, stamped per point at ingest) so a
caller only sees their own documents. Off by default (the lab is single-tenant). See
[`src/rag-service/app/retriever.py`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/src/rag-service/app/retriever.py).

**Pod Security Admission (restricted)** — The Kubernetes built-in that enforces the `restricted` Pod
Security Standard on platform namespaces via the `pod-security.kubernetes.io/enforce|audit|warn:
restricted` labels (see
[`deploy/sandbox/base/namespace.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/sandbox/base/namespace.yaml)).
Sandbox and tenant-lab namespaces get these labels so workloads run non-root, drop capabilities, and
use a restricted seccomp profile.

**Promotion request** — A reviewed `ModelPromotionRequest` (under
`platform/model-catalog/promotion-requests/`) that authorizes moving a model to a target status. It
records `targetStatus`, `requestedBy`, `approvers`, and a `businessJustification`. A model cannot reach
`approved` or a gateway allowlist without a matching promotion request, provenance digest, and evidence.
See [Model governance](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/model-governance.md).

**Prompt secret detection** — An input-path guardrail that rejects requests appearing to carry
credential material (e.g. `private_key`, `github_token`, `bearer_token`), protecting coding-agent
prompts that may accidentally include repo files or env output. PII detectors (`email`, `us_ssn`,
`credit_card`) are built in but opt-in. A match returns HTTP 400 with `prompt_secret_detected` and
names the pattern without echoing the matched text. See
[Guardrails](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/guardrails.md).

## Q

**Quantization (FP8/AWQ)** — Serving model weights (and optionally the KV cache) at reduced precision
to fit a model onto fewer GPUs, exposed via `server.quantization`, `server.kvCacheDtype`, and
`server.gpuMemoryUtilization` in the vLLM chart. FP8 (Hopper/Ada) gives ~2x memory saving at small
quality cost; AWQ is 4-bit weights for Ampere (point `model.name` at a pre-quantized `…-AWQ`
checkpoint). Ready profiles ship at `deploy/clusters/customer/values/vllm-nvidia-fp8.yaml` and
`vllm-nvidia-awq.yaml`; re-validate quality with `make eval` after changing it. See
[GPU capacity](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/gpu-capacity.md).

## R

**Release gate** — A machine-checked readiness gate declared in `platform/slo/release-gates.yaml` that
verifies eval, load, restore, toolchain, SLO, quota, provenance, supply-chain, egress/retention, and
evidence-pack evidence before a customer handoff. Run the default gate with `make release-gate`. A
failed gate means the handoff evidence is incomplete or below threshold. See
[Release gates](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/release-gates.md).

**Response cache** — An exact-match, per-sandbox cache of non-streaming chat completions, keyed by
`(sandbox_id, canonical-payload)` so a repeated identical request skips the runtime and one tenant's
cached answer is never served to another. TTL + LRU bounded; `stream` is excluded from the key and
streaming responses are never cached. Off by default (`responseCache` in
[`deploy/charts/inference-gateway/values.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/charts/inference-gateway/values.yaml));
use the Redis backend for a cache shared across replicas. See
[`src/inference-gateway/app/cache.py`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/src/inference-gateway/app/cache.py).

**riskTier** — A model catalog field recording the potential harm if a model misbehaves, validated
against `{low, medium, high}` in
[`scripts/model-catalog.py`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/scripts/model-catalog.py).
Distinct from *dataClassification*: riskTier is about model behavior/impact, dataClassification is about
the sensitivity of the data the model touches. A model carries both, and they must match its provenance
record.

**RWX weight cache** — A persistent volume that stores downloaded vLLM model weights so they survive
pod restarts and scale-up instead of re-downloading a 100+ GB model into an `emptyDir` on every cold
start. Configured under `cache.persistence` in the vLLM chart; sharing weights across replicas requires
a `ReadWriteMany` (RWX) storage class, whereas `ReadWriteOnce` requires keeping `replicaCount` at 1 or
pre-baking weights into the image. Disabled by default. See
[GPU capacity](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/gpu-capacity.md).

## S

**Sandbox-id** — A lowercase, DNS-label-style identifier (e.g. `local-lab`, `team-a-lab`) that scopes a
request for tracing, budgets, and attribution. Callers send it as the `X-Sandbox-ID` header; the
gateway echoes it and forwards it to the runtime, and it keys sandbox budgets and the response cache.
For a tenant lab it equals the namespace sandbox id. See
[Traceability sandbox](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/traceability-sandbox.md).

**Strict release gate** — The stricter form of the release gate, run with `make release-gate-strict`
for customer demos, release reviews, restore-drill reviews, and production-readiness handoff. It fails
when a required gate falls back to checked-in `sample-*` evidence or when selected evidence is older
than `RELEASE_GATE_MAX_EVIDENCE_AGE_HOURS` (default 24h) — so the report proves the *current* build is
ready, not just that the report shape is valid. See
[Release gates](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/release-gates.md)
and [Proof](proof.md).

## T

**Tamper-evident audit chain** — A per-process SHA-256 hash chain linking the gateway's redacted audit
events (`h_i = SHA-256(h_{i-1} || canonical(record_i))`) so any edit, insertion, deletion, or reordering
is detectable by recomputation. The live construction matches the offline auditor/verifier reference in
`paper/evidence-model/audit_chain.py` byte for byte. The chain is per replica; cross-replica and
long-horizon integrity depend on log shipping and an external head commitment. See
[ADR 0006](adr/0006-tamper-evident-audit-hash-chain.md).

**Tensor-parallel** — Splitting a single model's tensors across the GPUs of one node via
`--tensor-parallel-size` (set through the vLLM chart's `extraArgs`/GPU profiles). Keep
`--tensor-parallel-size` equal to `accelerator.count`. For models too large for one node, combine it
with pipeline parallelism across nodes under a LeaderWorkerSet topology. See
[GPU capacity](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/gpu-capacity.md).

**Tenant onboarding** — Generating a repeatable, reviewed package for a customer team: both the tenant
sandbox controls (Namespace, quota, LimitRange, NetworkPolicy, trace contract, RBAC) and the
coding-agent workspace values. Driven by a spec under `tenants/onboarding/` via `make tenant-onboard`
(or `make tenant-onboard-regulated` for the offline profile), with output written under
`.out/tenants/<sandbox-id>/`. See
[Tenant labs](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/tenant-labs.md).
