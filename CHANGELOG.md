# Changelog

## Unreleased

### Removed

- Removed the Dependabot configuration and its repository-hygiene checks. Dependabot's single-package bumps could not satisfy the strict `make validate-full` gate (hashed lockfiles plus regenerated API, chart, and config snapshots), so dependency updates are handled manually with `pip-compile`.

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
