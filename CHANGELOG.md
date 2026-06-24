# Changelog

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
