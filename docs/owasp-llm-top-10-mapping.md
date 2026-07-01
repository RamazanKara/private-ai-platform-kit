# OWASP LLM Top 10 Control Mapping

This document maps each item in the OWASP Top 10 for LLM Applications to the concrete control that
already exists in this repository, or to an explicit accepted-residual-risk statement where the kit
delegates the control to the operator or does not implement it yet.

It is scoped to the same boundary as the rest of the kit: gateway and RAG service code, Helm charts,
Kubernetes policy, GitOps wiring, validation tooling, and runbooks. It does not cover controls that
live entirely in the customer's identity provider, secret manager, cluster CNI, or model store; those
are called out as customer-owned where relevant. Read it alongside [Threat model](threat-model.md),
which goes deeper on the AI-specific threats, and the [Production readiness matrix](production-readiness.md),
which lists the validation command behind each control.

The OWASP item numbering follows the published "OWASP Top 10 for Large Language Model Applications"
list (LLM01..LLM10). Coverage is stated per item; where a control is described as "operator-owned" or
"out of scope", treat the residual risk as accepted for the kit's boundary.

## Conventions

- "Control" means a mechanism that exists in this repo at a cited file. None of these make the
  underlying threat disappear; treat them as defense in depth, the same framing as the threat model.
- "Residual risk" means the part of the threat the listed controls do not address. Where the kit
  hands the control to the operator, that is stated explicitly.
- File paths are repo-relative. Environment variables map to gateway settings in
  `src/inference-gateway/app/settings.py` and RAG settings in `src/rag-service/app/settings.py`.

## Summary table

| OWASP item | Primary in-repo control | Coverage |
| --- | --- | --- |
| LLM01 Prompt Injection | Default-deny egress, read-only RAG, per-tenant + per-classification RAG filters, prompt secret detection, admission caps, output guardrail, adversarial safety release gate | Partial; model influence by injected content is residual |
| LLM02 Insecure Output Handling | Response-path output guardrail (flag / redact / block) scanning completions; output treated as untrusted downstream | Partial; downstream consumers must still sanitize |
| LLM03 Training Data / Model Poisoning | Model catalog allowlist, promotion requests, provenance with digests | Partial; upstream training integrity is out of scope |
| LLM04 Model Denial of Service | Admission limits, sandbox budgets, rate limiting, concurrency load-shed, batch caps | Strong at the gateway boundary |
| LLM05 Supply Chain | Pinned images, hashed locks, SBOM, Trivy gating, Cosign signing, Scorecard, Dependabot | Strong for kit-built artifacts |
| LLM06 Sensitive Information Disclosure | Hash-only audit redaction, prompt secret/PII detection, output guardrail redaction, moderations endpoint | Partial; residual model-side leakage on streaming |
| LLM07 Insecure Plugin / Tool Design | Tool count/size caps, agent-workspace RBAC, default-deny egress | Partial; tool semantics are caller-owned |
| LLM08 Excessive Agency | Workspace RBAC, default-deny + catalog egress, quotas, budgets | Partial; agent autonomy is residual |
| LLM09 Overreliance | Grounded RAG with provenance excerpts; RAGAS-style faithfulness / context-precision eval gate | Partial; lexical faithfulness proxy, not an LLM judge |
| LLM10 Model Theft | Signed images, RBAC, default-deny network, PVC encryption attestation | Partial; weight-exfil via inference is residual |

## LLM01 Prompt Injection

**Controls in this repo.**

- Direct (caller) prompt content is size- and shape-bounded by gateway admission in
  `Settings.validate_admission` (`src/inference-gateway/app/settings.py`): message count
  (`MAX_MESSAGES`), total prompt characters (`MAX_PROMPT_CHARS`), completion-token ceiling, temperature
  range, and a streaming on/off gate. Tool/function schemas are bounded separately by
  `_validate_tools` (count `MAX_TOOLS`, serialized size `MAX_TOOL_CHARS`) because they are
  attacker-influenced free-form JSON forwarded to the runtime.
- Prompt secret detection is on by default (`prompt_secret_detection_enabled=True`) and rejects inputs
  matching the built-in credential detectors in `BUILT_IN_SECRET_PATTERNS`
  (`src/inference-gateway/app/settings.py`) before any runtime forwarding, raising
  `prompt_secret_detected`. This applies to both chat (`validate_admission`) and embeddings
  (`validate_embedding_admission`).
- Indirect / RAG injection blast radius is constrained at the network and data layers. Coding-agent
  and tenant namespaces run a default-deny `NetworkPolicy` plus an explicit approved-egress policy
  (`deploy/charts/agent-workspace/templates/networkpolicy.yaml`); the only default egress is DNS,
  the gateway namespace, and the RAG namespace. Any external CIDR must cite a `catalogRef` reviewed in
  `platform/network/egress-catalog.yaml` (enforced at Helm render time by the `required` guard in that
  template, and at admission by the Kyverno `ai-platform-restrict-egress-cidrs` policy in
  `deploy/policies/kyverno/policies.yaml`, which denies `0.0.0.0/0`, `::/0`, and broad RFC1918
  supernets).
- The RAG corpus is mounted read-only and is loaded from `RAG_DOCUMENT_DIR`
  (`src/rag-service/app/retriever.py` `load_documents`); the service has no write path back into the
  knowledge set at request time.
- Per-tenant and per-classification retrieval scoping ship: `QdrantRetriever._query_filter`
  (`src/rag-service/app/retriever.py`) appends a tenant-owner match when
  `retrieval_tenant_isolation_enabled` is set (`RAG_RETRIEVAL_TENANT_ISOLATION_ENABLED`, matching the
  ingest-stamped `owner` field to the request's `X-Sandbox-ID`, fail-closed), and a `classification`
  allowlist match when `retrieval_allowed_classifications` is set
  (`RAG_RETRIEVAL_ALLOWED_CLASSIFICATIONS`, `src/rag-service/app/settings.py`). An operator enables the
  isolation and/or allowlist per tenant to get the scoping benefit.
- A response-path output guardrail inspects completions for injected exfiltration/credential material
  before they return to the caller (see LLM02).
- Adversarial coverage is exercised by evals: `platform/evals/coding-agent-suite.yaml` has a
  `prompt-injection-boundary` case, and the dedicated `platform/evals/safety-suite.yaml` red-team
  battery (jailbreak, indirect-injection-via-retrieved-doc, secret-exfiltration, bias) is gated by the
  `safety` release gate (`minRefusalRate`) in `platform/slo/release-gates.yaml`, so a promoted model
  must meet a measured injection/jailbreak-resistance bar.

**Residual risk (accepted).** None of the above guarantees the model is not *influenced* by injected
instructions carried in retrieved documents or workspace files; the controls reduce what an influenced
model can *reach* and *exfiltrate*, and the output guardrail (LLM02) catches leaked secrets/PII on the
response path but not every semantic injection. The mitigation stays operational: review which
documents enter the corpus, enable per-tenant isolation, keep agent egress narrow, and treat model
output as untrusted. This matches the "Indirect / RAG prompt injection" section of
[Threat model](threat-model.md).

## LLM02 Insecure Output Handling

**Controls in this repo.** A response-path output guardrail inspects the model completion before it is
returned or cached. `_apply_output_guardrail` (`src/inference-gateway/app/main.py`) runs the configured
credential/PII detectors and blocked-term denylist (`Settings.output_findings` /
`redact_output_text`, `src/inference-gateway/app/settings.py`) over each choice's assistant text and,
per `OUTPUT_GUARDRAIL_MODE`, either flags, redacts the matched spans, or blocks the content (setting
`finish_reason=content_filter`). It is applied before the response cache so a leaked secret is never
persisted, emits `inference_gateway_output_guardrail_total{action,route}`, and sets an
`X-Output-Guardrail` response header. It is configured via `guardrails.outputGuardrail` in the gateway
chart (`OUTPUT_GUARDRAIL_ENABLED` / `OUTPUT_GUARDRAIL_MODE` / `OUTPUT_GUARDRAIL_PATTERNS`) and covered
by `src/inference-gateway/tests/test_output_guardrail.py`. The `/v1/moderations` endpoint
(`moderate_text`) remains available as an explicit pre/post classifier. See the Output Guardrail
section of [Guardrails runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/guardrails.md).

**Residual risk (accepted).** The detectors are pattern-based, so the guardrail catches known
credential/PII/blocked-term shapes, not arbitrary sensitive content or a semantically malicious-but-
clean-looking payload. Streaming responses are detected-and-flagged only (bytes are already on the
wire); hard redact/block enforcement requires non-streaming. Consumers must still treat output as
untrusted: a coding agent executing returned commands, a UI rendering returned HTML/markdown, or a
service parsing returned JSON must do its own validation, encoding, and sandboxing (insecure output
handling downstream is caller-owned).

## LLM03 Training Data and Model Poisoning

**Controls in this repo.**

- Only catalog-approved model IDs pass the gateway. `Settings.validate_model` and the
  `ModelRoutingPolicy` (`src/inference-gateway/app/policy.py`) reject any model not in
  `ALLOWED_MODELS` / the routing policy, cross-checked against
  `platform/model-catalog/models.yaml`.
- Adding a model is a reviewed change: approved entries require a promotion request under
  `platform/model-catalog/promotion-requests/` and a governed provenance record in
  `platform/governance/model-provenance.yaml`, which requires `sourceUri`, `immutableRef`, `digest`,
  `license`, `dataClassification`, `riskTier`, `promotionRequest`, and `servingProfiles` (see its
  `spec.requiredEvidence`). `scripts/model-provenance.py` validates this in the strict release gate
  (`make model-provenance-check`).
- Each provenance record pins a digest and a `verificationMode` / `verificationCommand`. Ollama-library
  models pin the registry model-weights layer (reproducible via the embedded `curl | jq` command);
  Hugging Face source models carry a `source-reference` digest that the customer replaces with their own
  pinned model-store checksum before production (`notes` field on each artifact).
- Container images (not weights) are signed with keyless Cosign and verified at admission by the
  Kyverno `ai-platform-verify-project-images` policy (`deploy/policies/kyverno/policies.yaml`); see
  LLM05.

**Residual risk (accepted).** Digest verification proves *what artifact was pulled*, not that the
upstream training pipeline that produced those weights was clean. Weight-level backdoors and
training-data poisoning in the upstream model are out of scope for this kit and cannot be detected by
provenance. The `riskTier` and `dataClassification` fields exist so an operator can apply additional
review to higher-risk models; that review is operator-owned.

## LLM04 Model Denial of Service

**Controls in this repo.** Multiple independent ceilings at the gateway boundary
(`src/inference-gateway/app/main.py` and `settings.py`):

- Admission caps reject expensive single requests before any runtime call: message count, prompt
  characters, completion tokens, temperature, and tool count/size (`validate_admission`,
  `_validate_tools`).
- Per-sandbox cumulative budgets (`SANDBOX_BUDGET_ENABLED` and the `SANDBOX_*_BUDGET` ceilings)
  enforce request, prompt-character, and estimated-token limits per `X-Sandbox-ID` over a rolling
  window, backed by in-memory or shared Redis counters (`src/inference-gateway/app/budget.py`,
  `build_sandbox_budget_tracker`). Exhaustion returns 429 with `Retry-After`, not a generic client
  error (`_admission_status`).
- A short-window per-sandbox rate limiter (`RATE_LIMIT_ENABLED`, `src/inference-gateway/app/ratelimit.py`)
  bounds burst abuse and is checked after sandbox binding so it applies to the authenticated tenant,
  not the spoofable header.
- Concurrency load-shedding: when `MAX_CONCURRENT_REQUESTS > 0`, the middleware in
  `create_app.request_context` fast-fails excess in-flight requests with a 503 (`_overloaded_response`,
  `inference_gateway_load_shed_total`) rather than queuing them behind the httpx pool.
- The batch endpoint caps batch size (`MAX_BATCH_REQUESTS`) and bounds per-batch fan-out with a
  semaphore so one batch cannot saturate the upstream pool (`batches` in `main.py`).
- Cluster-side capacity controls back this: HPA/KEDA scaling, PodDisruptionBudgets, topology spread,
  and required CPU/memory requests-and-limits enforced by the Kyverno `require-requests-and-limits`
  rule (`deploy/policies/kyverno/policies.yaml`).

**Residual risk (accepted).** These bound abuse at the gateway; they do not size the runtime. The
operator must tune limits, budgets, and replica counts to actual GPU/CPU capacity and SLOs (see the
Sandbox budgets and Autoscaling rows of [Production readiness matrix](production-readiness.md)).

## LLM05 Supply Chain Vulnerabilities

**Controls in this repo.** The CI pipeline (`.github/workflows/ci.yml`) and supporting tooling:

- Runtime images use a pinned Alpine Python base and runtime-only dependencies; Python dependencies use
  hashed lockfiles (`make dependency-lock-check`).
- Trivy scans images and the repo and fail the build on HIGH/CRITICAL findings; results upload as SARIF.
  Local equivalents: `make image-scan`, `make repo-security-scan`, `make supply-chain-check`, which
  emit SBOM, SARIF, checksum, and summary evidence under `results/supply-chain/`.
- Images and Helm charts are signed with keyless Cosign over OIDC, and the signature is verified at
  admission by the Kyverno `ai-platform-verify-project-images` policy
  (`deploy/policies/kyverno/policies.yaml`), with the keyless `subject` restricted to this repo's CI
  workflow on `refs/heads/main` and `issuer` set to `token.actions.githubusercontent.com`. Provenance
  and SBOM attestations are published with releases.
- OpenSSF Scorecard runs in `.github/workflows/scorecard.yml`; triage guidance is in
  `runbooks/scorecard-triage.md`.
- All GitHub Actions are pinned to a full commit SHA (not a floating tag) per the threat model, and
  Dependabot is configured for dependency updates.

**Residual risk (accepted).** This covers artifacts the kit builds. Customer-supplied base images,
third-party charts, and model weights are outside the signing/scanning boundary; forks that republish
images must update `imageReferences` and the keyless subject/issuer to their own identity or admission
denies their images (see "Build / release pipeline trust boundary" in [Threat model](threat-model.md)).

## LLM06 Sensitive Information Disclosure

**Controls in this repo.**

- The audit log never records raw prompt or completion text. `_payload_fingerprint` and
  `_write_audit_log` (`src/inference-gateway/app/main.py`) store message count, roles, prompt character
  length, and a SHA-256 fingerprint of the canonical message structure only. The audit chain is
  tamper-evident: each record links `prev_hash`/`record_hash` over canonical JSON (`_chain_audit_event`),
  verifiable by the same auditor tooling. Redaction is asserted by the `test_audit_log_redacts_prompt_content`
  test (Prompt privacy row of [Production readiness matrix](production-readiness.md)).
- Prompt secret detection (LLM01) and the opt-in PII detectors (`email`, `us_ssn`, `credit_card` in
  `BUILT_IN_SECRET_PATTERNS`, enabled via `PROMPT_SECRET_PATTERNS`) reject credential/PII material on
  the inbound path before it reaches the runtime or the logs.
- `/v1/moderations` (`moderate_text`, `src/inference-gateway/app/settings.py`) classifies text against
  every credential and PII detector plus configured blocked terms, returning an OpenAI-shaped result a
  caller can use as a pre-submit check.
- On the outbound path, the output guardrail (LLM02) scans completions for credential/PII material and
  can redact or block them before they reach the caller or the response cache, so a model that emits a
  recognizable secret does not leak it downstream.
- Data residency follows the cluster: audit records are redacted-only and all data-bearing stores
  (Qdrant, agent PVCs) are pinned to the customer cluster (see "Data Residency And PII" in
  [Threat model](threat-model.md)). Retention is governed by `platform/governance/data-retention.yaml`
  (`make retention-check`).

**Residual risk (accepted).** Inbound detection is pattern-based and is not a guarantee against all PII
forms, and there is no outbound scan of completions, so a model can still emit sensitive content it was
trained on or retrieved (this is the LLM02 output-handling gap). Classifying ingested content and
setting retention is customer-owned.

## LLM07 Insecure Plugin and Tool Design

**Controls in this repo.**

- Tool/function definitions are forwarded to the runtime as free-form JSON but are bounded by
  `_validate_tools` (`src/inference-gateway/app/settings.py`): a cap on the number of tools
  (`MAX_TOOLS`) and on the serialized size (`MAX_TOOL_CHARS`), with type validation that each is a
  list. This stops a caller from smuggling an oversized payload past the prompt-character limit via the
  `tools`/`functions` fields. Tool call counts are recorded in the audit fingerprint (`tool_count`,
  `tool_call_count` in `_payload_fingerprint`).
- The execution environment for tools — the coding-agent workspace — is locked down by RBAC. The
  workspace `Role` (`deploy/charts/agent-workspace/templates/rbac.yaml`) grants only read verbs
  (`get`, `list`, `watch`) on pods, logs, events, configmaps, and PVCs; Job management is opt-in
  (`allowJobManagement`) and bound to the workspace ServiceAccount only, never the human viewer group.
- Tool-initiated network access is constrained by the same default-deny + catalog egress as LLM01
  (`deploy/charts/agent-workspace/templates/networkpolicy.yaml`).

**Residual risk (accepted).** The gateway bounds the *size and count* of tool definitions and audits
tool calls; it does not validate tool *semantics* or sandbox tool *execution* — tools run wherever the
caller runs them, and their authorization model is the caller's responsibility. Design tools with least
privilege and validate their inputs/outputs in the calling application.

## LLM08 Excessive Agency

**Controls in this repo.** The coding-agent workspace chart (`deploy/charts/agent-workspace/`) is the
agency-limiting boundary:

- Namespace isolation with a default-deny `NetworkPolicy` and an approved-egress policy whose only
  defaults are DNS, the gateway, and RAG; every external destination must cite a reviewed `catalogRef`
  in `platform/network/egress-catalog.yaml` (`networkpolicy.yaml`).
- Least-privilege RBAC (read-only by default, opt-in Job management scoped to the ServiceAccount;
  `rbac.yaml`).
- Resource quotas and limit ranges (`resourcequota.yaml`, `limitrange.yaml`) and per-sandbox gateway
  budgets/rate limits (LLM04) cap how much an agent loop can consume.
- Quota-to-chargeback wiring in `platform/governance/quota-plans.yaml` (`make quota-check`).

**Residual risk (accepted, and called out in the threat model).** These reduce what an agent *can do*
once influenced; they do not decide whether the agent *should* take an action. There is no
human-in-the-loop approval gate on agent actions in the kit. Keep egress narrow, keep RBAC read-only
where possible, and add approval gates in the calling agent framework. The egress catalog entries are
examples with expiry dates the operator must review and replace.

## LLM09 Overreliance

**Controls in this repo.** RAG responses are grounded: the RAG service returns retrieval excerpts with
document id, title, and source attribution (`build_context` in `src/rag-service/app/retriever.py`) so a
consumer can trace a grounded answer back to its source documents, and approved models carry an
`evalSummary` evidence reference in `platform/governance/model-provenance.yaml`. Beyond structural
prompt checks, `scripts/rag-eval.py` scores RAGAS-style **context precision** and answer
**faithfulness** (`context_precision_at_k`, `answer_support`) against ground-truth answers in
`platform/evals/rag-retrieval-suite.yaml`, with `minContextPrecision` / `minFaithfulness` thresholds so
an embedding, chunking, or prompt change that stops surfacing the supporting passages is caught (the
misnamed retrieval-only "grounding rate" is now `retrieval_hit_rate`).

**Residual risk (accepted).** The faithfulness scorer is a deterministic **lexical** groundedness proxy
(fraction of answer tokens supported by the retrieved context), not an LLM-judge or NLI model — it
catches gross drift, not subtle unsupported claims. The scorer is isolated so an LLM-judge can replace
it without changing the suite schema. Overreliance is still mitigated for consumers by source
attribution and human review; a consuming application must not treat model output as authoritative.

## LLM10 Model Theft

**Controls in this repo.**

- Container images are signed and verified at admission (LLM05), so an unsigned or wrong-identity image
  cannot run in AI namespaces (Kyverno `ai-platform-verify-project-images`).
- Network access to runtimes is default-deny: agent/tenant namespaces can reach only the gateway and
  RAG (`deploy/charts/agent-workspace/templates/networkpolicy.yaml`), and external egress is
  catalog-gated, so a compromised tenant cannot freely pull weights out of the cluster.
- RBAC restricts in-namespace access to read-only verbs by default (`rbac.yaml`).
- Model weights live on PVCs whose encryption-at-rest is attested via the
  `platform.ai/encryption-at-rest=true` label, checked by the Kyverno
  `ai-platform-pvc-encryption-at-rest` policy (`deploy/policies/kyverno/policies.yaml`; currently
  `Audit` mode, intended to flip to `Enforce` once every storage class encrypts at rest).
- API access to inference is authenticated (API-key SHA-256 digests or JWT/JWKS;
  `src/inference-gateway/app/main.py`, `jwt_auth.py`) and per-sandbox attributable in the audit chain.

**Residual risk (accepted).** These protect the weight *artifact* and restrict *network* exfiltration;
they do not stop model extraction via high-volume inference (distillation/extraction attacks against the
serving endpoint). Rate limiting and budgets (LLM04) raise the cost of such attacks but do not prevent
them. Encryption-at-rest enforcement and storage-class selection are customer-owned, as is the secret
backend behind the API-key hashes.

## MITRE ATLAS pointer

The OWASP LLM Top 10 is application-risk oriented. For adversary tactics and techniques against
ML/AI systems, cross-reference [MITRE ATLAS](https://atlas.mitre.org/) (Adversarial Threat Landscape
for Artificial-Intelligence Systems). The mappings above align with ATLAS tactics as follows:

- LLM01/LLM02/LLM07/LLM08 relate to ATLAS *ML Attack Staging* and *Execution* (prompt injection,
  tool abuse, agent actions).
- LLM03/LLM05 relate to ATLAS *Initial Access* and *ML Supply Chain Compromise* (poisoned models and
  dependencies).
- LLM06 relates to ATLAS *Exfiltration* and *Collection* (sensitive information disclosure).
- LLM10 relates to ATLAS *Exfiltration via ML Inference API* and *ML Model Theft*.

Use ATLAS to drive red-team scenarios and the kit's existing chaos and eval drills
(`make chaos-drill`, `make eval`) to exercise the controls listed here. ATLAS technique-level mapping
is not maintained in-tree; treat this as a starting pointer, not an exhaustive crosswalk.
