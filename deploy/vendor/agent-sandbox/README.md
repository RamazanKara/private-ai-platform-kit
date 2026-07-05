# Vendored: kubernetes-sigs/agent-sandbox

Upstream release manifests for the agent-sandbox controller, vendored per
[ADR 0009](../../../docs/adr/0009-adopt-agent-sandbox-workspace-runtime.md).
Install with `make agent-sandbox-install` (applies server-side; the CRDs
exceed the client-side last-applied annotation limit).

## v0.5.0 (vendored 2026-07-01)

Source:
<https://github.com/kubernetes-sigs/agent-sandbox/releases/tag/v0.5.0>

| File | SHA-256 |
| --- | --- |
| `v0.5.0/manifest.yaml` | `ccfe3649de8b33f0ee0ec635e2c7b40e1c468ad9fde6aac7c7379501327d9c4c` |
| `v0.5.0/extensions.yaml` | `a6dd82905f3f3a44d30d02e283b6eb10d22c5fc1455604cf905d0e76091686d1` |

Contents: namespace `agent-sandbox-system`, controller Deployment
`agent-sandbox-controller`, webhook Service, RBAC, and four CRDs:
`sandboxes.agents.x-k8s.io` (core, `v1beta1` storage) plus
`sandboxclaims`/`sandboxtemplates`/`sandboxwarmpools.extensions.agents.x-k8s.io`.

## Upgrading

1. Download `manifest.yaml` and `extensions.yaml` from the upstream release
   into a new `deploy/vendor/agent-sandbox/<version>/` directory.
2. Record SHA-256 checksums in this file; update the pin in
   `docs/version-matrix.md`.
3. Re-verify the CRD spec fields the kit depends on (see the
   [integration design](../../../docs/agent-sandbox-integration.md),
   "Upstream summary"); the API is `v1beta1` and may still change.
4. Follow `runbooks/release-verification.md` before promoting to customer
   profiles.
