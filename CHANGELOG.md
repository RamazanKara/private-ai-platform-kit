# Changelog

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
