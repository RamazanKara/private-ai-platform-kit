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
  anchor and forwarding the audit receipts to a SIEM for long-term hold — the CronJob example and
  the procedure ship in `runbooks/audit-chain.md`.

## 3. Platform Hardening

- Add IdP-specific examples and rotation drills for optional OIDC/JWT/JWKS validation.
- Keep model-catalog-driven runtime routing and per-sandbox admission policy covered by contracts.
- Expand runtime retry, timeout, readiness, and circuit-breaker fault-injection coverage.
- Expand streaming test coverage for Ollama and vLLM compatibility.

## 4. RAG Hardening

- Per-tenant retrieval isolation is enforced by default (`retrieval.tenantIsolation` on both the
  Qdrant and lexical backends, fail-closed on a missing/unasserted tenant). **Remaining:** add
  per-caller identity to the RAG service itself — audience-bound token verification (JWKS/audience
  validation, mirroring the gateway's `jwt_auth`) or per-tenant API keys — so the tenant is derived
  from a verified claim on the RAG service rather than a trusted `X-Sandbox-ID` header set upstream.
- Replace demo hashed-vector behavior with a pluggable embedding provider interface.
- Expand collection migration dry runs and rollback guidance around the reviewed ingestion job, source metadata, collection versioning, and Qdrant readiness checks.
- Add examples for customer document-source approvals and retention classes.

## 5. Helm And Distribution

- Keep OCI chart publishing green (tag builds push every chart to GHCR; see ADR 0008).
- Keep chart READMEs and values tables current.
- Add minimal, local, and customer profile examples for each major chart.

## 6. Code Quality And Observability

- Enforced: Ruff lint, Ruff format, and mypy run in `make validate` and CI; CodeQL provides Python SAST.
- In place: public-API docstrings across both services, `helm test` connection probes for the gateway and RAG charts, per-service Grafana dashboards, and a `make dashboard-check` gate that fails when a dashboard references a metric the service does not emit.
- Ratchet `make coverage` floors upward as gateway and RAG test coverage grows (currently 85% gateway, 84% RAG).
- Expand mypy strictness (stricter optional handling) once the baseline holds across releases.
- Expand runtime streaming and circuit-breaker fault-injection coverage for Ollama and vLLM.

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

- `runtime`: Multi-node distributed serving requires the LeaderWorkerSet (or Ray) operator and
  a per-cluster GPU topology — the kit ships the working LWS example and the pipeline-parallel
  flags (runbooks/gpu-capacity.md); the operator installs and sizes it.
- `runtime`: LoRA/adapter *artifacts* and the embedding/serving model *weights* are the
  customer's to host and pin — the kit ships the serving flags, catalog governance, and a
  source-reference provenance digest the customer replaces with their pinned model-store checksum.
- `dx`: A standalone admin/usage *console UI* is a separate web application; the kit ships the
  `/v1/usage` data layer, the metrics, and the client SDK it would build on.
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
