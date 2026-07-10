# Roadmap

This roadmap is ordered by what most improves open-source evaluation quality.

## 1. First-Run Experience

- Keep `make quickstart` green on fresh Ubuntu developer machines and GitHub-hosted runners.
- Add screenshots and short terminal recordings for quickstart, Argo CD sync, Grafana, and evidence generation.
- Expand troubleshooting for common Docker, kind, kubectl, Helm, model-pull, and port-forward failures.

## 2. Production Proof

- Publish current strict evidence for every release.
- Keep scheduled `validate-full`, local E2E, image scan, supply-chain, load-test, and strict release-gate checks green.
- Keep documented verification current for SBOMs, checksums, Cosign signatures, provenance attestations, and OpenSSF Scorecard findings.
- Shipped (v0.20.0): the operator audit-chain verifier (`make audit-verify`, stdlib-only, offline,
  `--selftest` wired into `make validate`) and head anchoring (`make audit-anchor`,
  `audit-verify --anchor`) with a hash-covered per-replica `chain_id`. This closes the
  head-anchoring gap ADR 0006 flagged. **Remaining (operator-owned):** committing/exporting the
  anchor and forwarding the audit receipts to a SIEM for long-term hold. The CronJob example and
  the procedure ship in `runbooks/audit-chain.md`.

## 3. Platform Hardening

- Shipped (v0.22.0): native Anthropic Messages API (`POST /v1/messages`), translated to/from the OpenAI chat shape and routed through the same governance path as chat (non-streaming this release; a translation sidecar remains the option for streaming).
- Shipped (v0.23.0-v0.26.0): OpenAI Responses API plus opt-in tenant-scoped, TTL-bounded server-side state (`store`, `previous_response_id`, retrieve/delete/input-items). Background and Responses-shaped streaming remain out of scope.
- Add IdP-specific examples and rotation drills for optional OIDC/JWT/JWKS validation.
- Shipped (v0.22.0): the gateway's JWT signature/claim core now runs on the maintained PyJWT library (`jwt.decode`, algorithm pinned to the configured allowlist), replacing the in-tree RSA/EC verification while preserving the JWKS cache and 503-vs-401 semantics behind the same `JwtVerifier` interface.
- Keep model-catalog-driven runtime routing and per-sandbox admission policy covered by contracts.
- Expand runtime retry, timeout, readiness, and circuit-breaker fault-injection coverage.
- Expand streaming test coverage for Ollama and vLLM compatibility.

## 4. RAG Hardening

- Per-tenant retrieval isolation is enforced by default (`retrieval.tenantIsolation` on both the
  Qdrant and lexical backends, fail-closed on a missing/unasserted tenant), and the RAG service now
  derives per-caller identity from its **own** audience-bound token verification (`auth.jwt`, `RAG_JWT_*`,
  covering JWKS/issuer/audience/exp/nbf with an alg allowlist, mirroring the gateway's `jwt_auth`): the tenant
  comes from a verified claim on the RAG service itself (a contradicting `X-Sandbox-ID` header is rejected
  403, a missing token fails closed 401 when required), with header-trust as the fallback when JWT is off.
- Shipped: pluggable hash and OpenAI-compatible embedding providers, plus a dedicated customer vLLM embedding release and governed model provenance.
- Expand collection migration dry runs and rollback guidance around the reviewed ingestion job, source metadata, collection versioning, and Qdrant readiness checks.
- Add examples for customer document-source approvals and retention classes.

## 5. Helm And Distribution

- Keep digest-bound OCI chart publishing green (tag builds promote the tested images and push
  every chart plus Artifact Hub metadata to GHCR; see ADR 0008).
- Keep PyPI Trusted Publishing, the Python 3.11-3.14 SDK matrix, package checksums, and release
  artifacts green.
- Keep versioned `mike` documentation (`development`, `latest`, and immutable release aliases)
  available through GitHub Pages.
- Keep chart READMEs and values tables current.
- Add minimal, local, and customer profile examples for each major chart.

## 6. Code Quality And Observability

- Enforced: Ruff lint, Ruff format, and mypy run in `make validate` and CI; CodeQL provides Python SAST.
- In place: public-API docstrings across both services, `helm test` connection probes for the gateway and RAG charts, per-service Grafana dashboards, and a `make dashboard-check` gate that fails when a dashboard references a metric the service does not emit.
- Ratchet `make coverage` floors upward as gateway and RAG test coverage grows (currently 85% gateway, 84% RAG).
- Expand mypy strictness (stricter optional handling) once the baseline holds across releases.
- Expand runtime streaming and circuit-breaker fault-injection coverage for Ollama and vLLM.

## Deliberate Deferrals

These were evaluated and deliberately deferred, not overlooked. They are recorded here so the
decision reads as intentional and can be revisited with evidence.

- **Semantic (embedding-similarity) response caching.** The gateway response cache
  (`responseCache`, `src/inference-gateway/app/cache.py`) is **exact-match only**: a hit requires
  the same sandbox and a byte-identical request payload. Semantic caching (embedding the prompt
  and serving a cached completion for a *similar*, not identical, prompt) was considered and
  deferred. The reasoning: for the agent and tool-use traffic this gateway is built around, a
  near-miss is a correctness/staleness hazard, not a convenience. Two prompts a similarity
  threshold treats as equivalent routinely demand different answers (a changed file path, an
  off-by-one arg, a different tenant's context, a "now do the opposite" turn), so a semantic hit
  risks returning a confidently wrong prior completion; the hit rate that would justify it is
  exactly the regime where staleness bites hardest. It also adds an embedding call and a vector
  lookup on the hot path, weakens the audit story (what was served vs. what was requested), and
  interacts badly with the output guardrail (a cached hit skips re-inspection). Exact-match
  caching keeps the semantics obvious and safe. Revisit only behind an explicit opt-in with a
  conservative threshold, per-sandbox scoping, TTL, and cache-hit audit, and only for workloads
  (e.g. FAQ-style retrieval) where a near-duplicate answer is acceptable.

## Seed Issue List

Use these labels when opening public issues: `good first issue`, `help wanted`, `security`, `docs`, `helm`, `runtime`, `rag`, `tenant`.

Run `scripts/seed-roadmap-issues.sh` to print the seed commands, or `scripts/seed-roadmap-issues.sh --create --repo owner/name` to open them with `gh`.

- `docs`: Add quickstart screenshots for local lab and evidence generation.
- `runtime`: Add IdP-specific OIDC examples and JWKS rotation drills for gateway JWT auth.
- `runtime`: Expand streaming compatibility tests for Ollama and vLLM.
- `rag`: Add Qdrant migration dry-run and rollback walkthrough.
- `helm`: Add minimal, local, and customer install profiles to each chart README.
- `tenant`: Add more tenant onboarding examples for regulated and offline teams.
- `tenant`: Add GPU-backed coding-agent tenant walkthrough.
- `security`: Add Scorecard triage and remediation guidance.

## Remaining External / Operator-Owned Work

The feature-gap remediation pass closed the audit findings in-tree (see the CHANGELOG v0.12.0 and v0.13.0 entries):
progressive delivery (canary/A-B/shadow), the batch API, age-based retention purge,
encryption-at-rest policy, the usage+cost API, self-service onboarding apply, the first-party
SDK, and embedding-model governance all ship. What remains is inherently external to the kit's
"manifests, charts, service code, validation tooling, and runbooks" boundary:

- `distribution`: PyPI's pending Trusted Publisher and Artifact Hub's OCI repository entry each
  require a one-time account-owner registration. The release workflows, protected GitHub
  environment, package metadata, OCI discovery metadata, and exact registration instructions
  ship in-tree; the assigned Artifact Hub `repositoryID` is recorded after registration.
- `runtime`: Multi-node distributed serving requires the LeaderWorkerSet (or Ray) operator and
  a per-cluster GPU topology; the kit ships the working LWS example and the pipeline-parallel
  flags (runbooks/gpu-capacity.md), and the operator installs and sizes it.
- `runtime`: LoRA/adapter *artifacts* and the embedding/serving model *weights* are the
  customer's to host and pin; the kit ships the serving flags, catalog governance, and a
  source-reference provenance digest the customer replaces with their pinned model-store checksum.
- `dx`: A richer multi-tenant administration product remains separate; the kit now ships the
  opt-in read-only `/console`, `/v1/usage`, metrics, and the client SDK.
- `security`: Flipping the encryption-at-rest Kyverno policy from Audit to Enforce, and scheduling
  the age-based retention purge as a CronJob, are per-environment operational decisions; the
  policy, the labels, and the purge command ship ready to use.
- `security`: In-cluster encryption in transit is delegated to a CNI/mesh control. The kit ships an
  opt-in overlay (service-mesh mTLS, Cilium WireGuard/IPsec, or cert-manager TLS) under
  `deploy/clusters/customer/mtls/`; installing and operating the mesh/CA is operator-owned.
- `dr`: A secondary cluster, multi-region failover, and warm-standby topology are out of scope for
  this single-cluster, local-first kit. `runbooks/disaster-recovery.md` documents the single-cluster
  recovery sequence, RPO/RTO targets, and per-store data-loss windows; provisioning the off-cluster
  backup target (Velero `BackupStorageLocation`/`VolumeSnapshotLocation`), a standby cluster, and
  cross-region replication is the operator's responsibility.
- `security`: Runtime (behavioral) threat detection ships as an opt-in Falco/Tetragon Argo
  application (`deploy/observability/runtime-security.yaml`) plus a runbook; installing the
  privileged DaemonSet and tuning rules is a per-cluster operator decision.
