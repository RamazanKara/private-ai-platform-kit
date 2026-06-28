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

## 6. Code Quality

- Enforced: Ruff lint, Ruff format, and mypy run in `make validate` and CI; CodeQL provides Python SAST.
- Ratchet `make coverage` floors upward as gateway and RAG test coverage grows.
- Expand mypy strictness (typed public APIs, stricter optional handling) once the baseline holds across releases.

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
