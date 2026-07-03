# Security Overview

This page is the single entry point to the kit's security surface. It is an **index, not a set of new
controls**: every item below is a one-paragraph summary that points at the authoritative source in the
tree. Nothing here changes behavior; if a summary and a linked source ever disagree, the linked source
wins. Read it alongside the [Production readiness matrix](production-readiness.md), which lists the
validation command behind each control.

The framing throughout the kit applies here too: these are mechanisms, defense in depth, not
guarantees. Each authoritative page states its own residual risk and where the kit delegates the
control to the operator.

## Threat model, mappings, and reporting

- **Threat model.** The [Threat model](threat-model.md) covers the local lab and customer-owned
  Kubernetes pattern: assets, trust boundaries, primary and AI-specific threats (indirect/RAG prompt
  injection, model-artifact tampering, the build/release pipeline boundary), the current controls, and
  the Required Customer Hardening list. It is the deepest treatment of the AI-specific threats and the
  place to start.
- **OWASP LLM Top 10 mapping.** The [OWASP LLM Top 10 mapping](owasp-llm-top-10-mapping.md) maps each
  LLM01..LLM10 item to the concrete in-repo control at a cited file, or to an explicit
  accepted-residual-risk statement where the control is operator-owned. It also carries a MITRE ATLAS
  pointer for adversary tactics.
- **AI governance crosswalk.** The [AI governance crosswalk](ai-governance-crosswalk.md) maps the same
  controls to the NIST AI RMF, the EU AI Act (Regulation (EU) 2024/1689), and ISO/IEC 42001:2023, and
  defines the `riskTier` (`low | medium | high`) semantics used across the model catalog and provenance.
  A framework citation means "this control contributes evidence toward that obligation," not "this
  deployment is conformant."
- **Vulnerability reporting.** [SECURITY.md](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/SECURITY.md)
  is the security policy: report privately through GitHub Private Vulnerability Reporting (the repo's
  Security tab, "Report a vulnerability") or, as an alternate, email security@fluentorbit.de. It also
  lists the supported surface, the coordinated-disclosure window (default 90 days), and the handling
  rules (hashes only, no raw prompts logged, hashed lockfiles, signed-by-digest promotion).

## Gateway controls

The inference gateway (`src/inference-gateway`) is the request/response chokepoint and carries most of
the runtime controls. All are documented per-control in the OWASP mapping and the threat model.

- **Authentication.** API-key SHA-256 digest auth (`API_KEY_AUTH_ENABLED`, `API_KEY_SHA256S`) and/or
  JWT bearer auth with JWKS-backed HS256/RS256/ES256 verification
  ([`src/inference-gateway/app/jwt_auth.py`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/src/inference-gateway/app/jwt_auth.py)).
  Signature and standard-claim checks are performed by the maintained
  [PyJWT](https://pyjwt.readthedocs.io/) library (`jwt.decode`), with the verifying algorithm pinned
  to the configured allowlist rather than read from the token header (alg-confusion defense); the
  gateway retains its own JWKS fetch, last-known-good cache, and the 503-vs-401 distinction (issuer
  unreachable with no cached keys is a retryable 503, a rejected token is a 401).
  Optional **API-key records** (`API_KEY_RECORDS_PATH`,
  [`src/inference-gateway/app/key_records.py`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/src/inference-gateway/app/key_records.py))
  add per-key scopes, expiry (`api_key_expired` on a stale key), sandbox binding, and budget overrides
  beside the flat hash list; a malformed records file fails the gateway closed at startup. Wiring auth to
  the enterprise identity boundary and backing the hashes/records with the customer secret manager is
  operator-owned (RS256/ES256 preferred for customer IdPs). **Tenant binding:** with a JWT `tenantClaim`
  or a sandbox-bound key record, the sandbox is taken from the verified identity, not the client
  `X-Sandbox-ID` header — a contradicting header is rejected (`sandbox_identity_mismatch`), and the
  read-only `GET /v1/usage` and `GET /v1/sandbox/budget` endpoints are thereby scoped to the caller's own
  tenant. Without a binding the gateway is header-trusted (the documented single-tenant/local default).
  Human SSO for the operator dashboards (Grafana, Argo CD) is distinct from this machine auth; see the
  [API access runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/api-access.md).
- **Admission limits.** `Settings.validate_admission` and `_validate_tools`
  ([`src/inference-gateway/app/settings.py`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/src/inference-gateway/app/settings.py))
  bound message count, prompt characters, completion tokens, temperature, streaming, and tool
  count/size before any runtime call (OWASP LLM04).
- **Input prompt-secret detection.** On by default (`prompt_secret_detection_enabled`), it rejects
  credential material against `BUILT_IN_SECRET_PATTERNS` before forwarding, with opt-in PII detectors
  (email, US SSN, credit card). The `/v1/moderations` endpoint exposes the same rule-based classifier as
  an explicit pre-submit check (OWASP LLM01/LLM06).
- **Output guardrail.** Shipped in v0.13.0. `_apply_output_guardrail`
  ([`src/inference-gateway/app/main.py`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/src/inference-gateway/app/main.py))
  inspects each completion for leaked credentials/PII/blocked content and **flags, redacts, or blocks**
  it per `OUTPUT_GUARDRAIL_MODE` before the response is cached, audited, or returned. It emits an
  `inference_gateway_output_guardrail_total` metric and an `X-Output-Guardrail` header; streaming
  responses are detected-and-flagged at end-of-stream (redact/block requires non-streaming). Disabled by
  default (`OUTPUT_GUARDRAIL_ENABLED=false`). See the
  [Guardrails runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/guardrails.md)
  (OWASP LLM02/LLM06).
- **Sandbox budgets.** Per-`X-Sandbox-ID` cumulative request/prompt-char/estimated-token ceilings over a
  rolling window (`SANDBOX_BUDGET_ENABLED`,
  [`src/inference-gateway/app/budget.py`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/src/inference-gateway/app/budget.py)),
  backed by in-memory or shared Redis counters; exhaustion returns 429 with `Retry-After`. See the
  [Budget controls runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/budget-controls.md).
- **Rate limiting and load-shedding.** A short-window per-sandbox rate limiter (`RATE_LIMIT_ENABLED`,
  [`src/inference-gateway/app/ratelimit.py`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/src/inference-gateway/app/ratelimit.py))
  checked after sandbox binding, plus concurrency load-shedding (`MAX_CONCURRENT_REQUESTS`) and a
  batch-size cap (OWASP LLM04).

## Supply chain

CI ([`.github/workflows/ci.yml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/.github/workflows/ci.yml))
and supporting tooling secure the artifacts the kit builds: pinned Alpine Python base images and hashed
Python lockfiles, **SBOM** generation, **Trivy** image/repo scans that fail the build on HIGH/CRITICAL,
keyless **Cosign** signing of images and Helm charts over OIDC, **OpenSSF Scorecard**
([`.github/workflows/scorecard.yml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/.github/workflows/scorecard.yml)),
**CodeQL**, and all GitHub Actions pinned to full commit SHAs with Dependabot updates. Local evidence:
`make supply-chain-check`, `make image-scan`, `make repo-security-scan`. Customer-supplied base images,
third-party charts, and model weights are outside this boundary; see OWASP LLM05 and the "Build /
release pipeline trust boundary" section of the threat model.

## Cluster policy and isolation

- **Policy-as-code (Kyverno) + Pod Security Admission.** Kyverno policies
  ([`deploy/policies/kyverno/policies.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/policies/kyverno/policies.yaml))
  verify signed images at admission (`ai-platform-verify-project-images`, Enforce), restrict egress
  CIDRs (`ai-platform-restrict-egress-cidrs`), require CPU/memory requests-and-limits, and audit PVC
  encryption-at-rest. These are backed by apiserver-native **Pod Security Admission** (restricted
  profile) on the platform data-plane namespaces (v0.13.0). See the
  [Policy blocked-deploy runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/policy-blocked-deploy.md).
- **Network default-deny + encryption-in-transit.** Coding-agent and tenant namespaces run a
  default-deny `NetworkPolicy` plus an approved-egress policy whose only defaults are DNS, the gateway,
  and RAG; any external CIDR must cite a reviewed `catalogRef` in
  [`platform/network/egress-catalog.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/platform/network/egress-catalog.yaml).
  NetworkPolicies restrict who may connect but do **not** encrypt: the in-cluster data plane is plaintext
  HTTP by default. Encryption in transit is an opt-in, operator-owned overlay (service-mesh mTLS,
  Cilium WireGuard/IPsec, or cert-manager TLS) —
  [`deploy/clusters/customer/mtls/`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/clusters/customer/mtls/README.md).
  Treat enabling it as required before handling regulated data in a multi-tenant cluster.
- **Runtime threat detection.** Admission and NetworkPolicies are preventive; they do not observe
  post-admission behavior. An opt-in **Falco** runtime-detection layer (Argo app + runbook) provides
  detective coverage —
  [runtime-threat-detection.md](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/runtime-threat-detection.md).
- **Per-tenant RAG isolation.** The RAG service scopes retrieval to the caller's tenant via the
  ingest-stamped `owner` payload field, complementing the existing per-classification retrieval
  allowlist. It is **enabled by default** (`RAG_RETRIEVAL_TENANT_ISOLATION_ENABLED` defaults true,
  [`src/rag-service/app/settings.py`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/src/rag-service/app/settings.py));
  the bundled single-tenant local lexical profile is the only shipped configuration that turns it off
  (`retrieval.tenantIsolation.enabled: false` in the chart), and multi-tenant/customer profiles keep it
  on. **Both retrieval backends enforce it:** the Qdrant path appends an `owner` match to the query
  filter, and the lexical path (whose corpus is stamped with the default sandbox id) applies the same
  owner scoping. **It fails closed:** a tenant with no matching documents — or a request that did not
  explicitly assert `X-Sandbox-ID` (only the server-side default) — receives no documents rather than
  the whole corpus, and a missing-tenant query never runs an unfiltered search. **Verified-claim tenant
  binding.** The RAG service can now derive the tenant from its **own** verified token rather than a
  trusted header: with `auth.jwt.enabled` (`RAG_JWT_*`, mirroring the gateway's `jwt_auth` — JWKS with a
  last-known-good cache, an `HS256`/`RS256`/`ES256` allowlist pinned so alg-confusion is rejected, and
  issuer/audience/exp/nbf enforcement) the caller's tenant is taken from the verified `tenantClaim` and
  used for the isolation filter, so a contradicting `X-Sandbox-ID` header is rejected
  (`sandbox_identity_mismatch`, 403) and, when `required`, a missing/invalid token fails closed (401)
  while an unreachable issuer with no cached keys returns 503, not a false denial. This closes the last
  gap for direct callers; header-trust remains the fallback when JWT is off (the default, and the only
  shipped configuration on the single-tenant local lab). Without a binding the RAG service is
  header-trusted, so isolation is only trustworthy when the `X-Sandbox-ID` header comes from a trusted
  path — gateway-fronted traffic that derives it from a verified JWT tenant claim, or a workspace egress
  proxy that stamps it — not a direct caller under the shared key asserting another tenant's id. See the
  [RAG service runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/rag-service.md),
  the [Vector RAG runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/vector-rag.md),
  and OWASP LLM01.

## Audit and evaluation evidence

- **Audit hash chain.** The gateway audit log never records raw prompt or completion text: it stores
  message count, roles, prompt character length, and a SHA-256 fingerprint of the canonical message
  structure, with each record linked by `prev_hash`/`record_hash` over canonical JSON so the chain is
  tamper-evident (`_write_audit_log`, `_chain_audit_event` in
  [`src/inference-gateway/app/main.py`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/src/inference-gateway/app/main.py);
  asserted by `test_audit_log_redacts_prompt_content`). Retention is governed by
  [`platform/governance/data-retention.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/platform/governance/data-retention.yaml)
  (OWASP LLM06).
- **Safety and faithfulness evals.** The eval harness includes an adversarial safety/jailbreak/bias
  suite ([`platform/evals/safety-suite.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/platform/evals/safety-suite.yaml))
  gated by a `safety` release gate, and **RAGAS-style** context-precision and answer-faithfulness scoring
  in [`scripts/rag-eval.py`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/scripts/rag-eval.py)
  (both shipped in v0.13.0), which fails when aggregate metrics fall below the suite thresholds
  (`minFaithfulness`, `minContextPrecision`). Run with `make eval` / `make eval-local` (OWASP LLM09).
