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

## 3. Platform Hardening

- Add IdP-specific examples and rotation drills for optional OIDC/JWT/JWKS validation.
- Keep model-catalog-driven runtime routing and per-sandbox admission policy covered by contracts.
- Expand runtime retry, timeout, readiness, and circuit-breaker fault-injection coverage.
- Expand streaming test coverage for Ollama and vLLM compatibility.

## 4. RAG Hardening

- Replace demo hashed-vector behavior with a pluggable embedding provider interface.
- Expand collection migration dry runs and rollback guidance around the reviewed ingestion job, source metadata, collection versioning, and Qdrant readiness checks.
- Add examples for customer document-source approvals and retention classes.

## 5. Helm And Distribution

- Publish Helm charts as OCI artifacts.
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

## Deferred Larger Efforts

The recent feature-gap pass closed most audit findings (see CHANGELOG Unreleased). The
remaining items below are genuinely multi-week, large-system efforts; they are tracked here
so they are not lost:

- `runtime`: Progressive model delivery (canary / shadow / weighted A-B) and full LoRA /
  adapter *lifecycle governance* (registration, promotion, eval) — the chart already serves
  LoRA via extraArgs; the gap is the governance/rollout system. The project cedes canary to
  KServe today.
- `runtime`: Multi-node distributed serving (pipeline-parallel, LeaderWorkerSet, or Ray) for
  models larger than one node, and a batch / async inference job API.
- `tenant`: A self-service onboarding *controller* (the spec-driven render-review-apply
  pipeline and the offboarding plan generator exist; the gap is a reconciling controller/API).
- `dx`: A first-party client SDK package and an admin/usage console UI (a client-examples doc
  ships today; the console is a separate web app).
- `security`: Encryption-at-rest enforced via Kyverno on platform data stores (provider-neutral
  enforcement requires a labeling/storage-class convention), and full age-based RAG retention
  purge (needs an ingestion timestamp in the chunk payload; today erasure is delete-by-source
  plus collection-version rotation).
- `rag`: Promote the governed reference embedding model to `approved` with a real provenance
  digest + promotion request, replacing the hashed-vector default in the shipped profiles.
