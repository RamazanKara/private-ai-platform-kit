# Validation Toolchain Sample Summary

Generated: `2026-05-31T00:00:00Z`
Profile: `validate`

Summary: 0 missing required tools, optional strict tools may be missing on a workstation.

Install strict tools on Linux, WSL, or CI with `make toolchain-install`, then add `.tools/bin` to `PATH`.

| Tool | Role | Status | Purpose |
| --- | --- | --- | --- |
| python3 | required | found | Runs service tests, YAML checks, readiness gates, report generators, and local automation. |
| helm | required | found | Lints and renders all platform charts and customer overlays. |
| restore-drill | optional | found | Validates restore-drill configuration and runs application-data restore evidence. |
| syft | optional | found | Generates filesystem and image SBOM evidence. |
| docker | optional | found | Builds local service images and runs kind nodes. |
| kind | optional | found | Creates the local Kubernetes reference cluster. |
| kubectl | optional | found | Applies manifests, inspects local readiness, and runs live evidence checks. |
| go | optional | found | Builds or installs Go-based validation utilities. |
| kubeconform | optional | missing | Validates Kubernetes YAML against schemas before customer handoff. |
| kyverno | optional | missing | Runs policy-as-code tests for labels, pod security, resources, image tags, and signature policy. |
| k6 | optional | missing | Validates and runs gateway load-test scenarios. |
| argocd | optional | missing | Validates local GitOps client compatibility for Argo CD workflows. |
| cosign | optional | missing | Verifies image-signing workflows and customer promotion gates. |
| trivy | optional | missing | Runs filesystem, secret, config, and image vulnerability scans. |
