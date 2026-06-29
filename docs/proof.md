# Project Proof

This page records what the project can prove from the repository and what must be regenerated for a real release or customer handoff.

## Current Proof Sources

- `make validate` checks service tests, Helm rendering, contracts, governance, evidence inputs, release gates with sample fallback, and repo hygiene.
- `make validate-full` requires the strict validation toolchain: kubeconform, Kyverno CLI, restore-drill, k6, Syft, Argo CD CLI, Cosign, and Trivy.
- `make image-scan` builds runtime images, writes SBOMs, runs Trivy HIGH/CRITICAL scans, writes checksums, and validates supply-chain summaries.
- `make loadtest-local` runs k6 against a local gateway backed by an OpenAI-compatible mock runtime.
- `make evidence LIVE=1` adds live Kubernetes readiness checks after the local lab is synced.
- `make release-gate-strict` rejects checked-in sample evidence and stale evidence.
- GitHub Actions publish Cosign signatures, SLSA build provenance attestations, SBOM attestations, and OpenSSF Scorecard SARIF for public release review.

## Release Evidence

For release reviews, attach or link:

- OpenAPI snapshots from `platform/api-contracts/`
- Configuration snapshots from `platform/config-contracts/`
- Current eval, load, restore, toolchain, SLO, quota, egress, retention, model-provenance, evidence-pack, and supply-chain reports under `.out/results/`
- SBOMs, SARIF files, checksums, signed image digests, provenance attestations, SBOM attestations, and Scorecard findings from GitHub Actions

## Supported Tool Versions

The source of truth is [platform/tools/validation-toolchain.yaml](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/platform/tools/validation-toolchain.yaml). Run:

```bash
make toolchain-report TOOLCHAIN_PROFILE=strict
```

The generated JSON and Markdown reports show which tools were present, missing, and used for proof.

## What Sample Evidence Means

Checked-in files named `sample-*` prove report shape and gate behavior. They do not prove the current release. Strict gates must use freshly generated non-sample artifacts.
