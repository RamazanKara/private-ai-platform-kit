# 0009. Adopt kubernetes-sigs/agent-sandbox as the coding-agent workspace runtime

- Status: Proposed
- Date: 2026-07-01
- Deciders: Ramazan Kara (maintainer)

## Context

The coding-agent workspaces pillar isolates agent workloads at the namespace
level: `deploy/charts/agent-workspace` provisions a Pod Security
Admission-restricted namespace with default-deny NetworkPolicies, catalog-gated
egress (`platform/network/egress-catalog.yaml`), ResourceQuota, LimitRange,
least-privilege RBAC, and PVC-backed storage. The platform-level sandbox is a
logical construct: a validated `X-Sandbox-ID` that the inference gateway binds
to budgets, metrics, and tamper-evident audit events
(`src/inference-gateway/app/main.py`).

This gives strong *tenant* isolation but no *kernel* isolation. An agent that
executes untrusted or model-generated code shares the node kernel with other
workloads; the only barriers are the container runtime and the restricted pod
security profile. No ADR records this trade-off; it was an implicit choice made
before a credible upstream alternative existed.

That alternative now exists. `kubernetes-sigs/agent-sandbox` (SIG Apps) reached
v0.5.0 in June 2026 and graduated its core API to `agents.x-k8s.io/v1beta1`
(`Sandbox`), with `SandboxTemplate`, `SandboxClaim`, and `SandboxWarmPool`
under `extensions.agents.x-k8s.io/v1beta1`. It provides secure-by-default
networking on the template path (cluster-internal IPs and metadata endpoints
blocked since v0.2.1; a directly created `Sandbox` gets no upstream
NetworkPolicy — verified on `kind`, 2026-07-01), lifetime bounds
(`shutdownTime`/`shutdownPolicy` on sandboxes, `ttlSecondsAfterFinished` on
pooled claims), suspend/resume, warm pools for ~1 s allocation, and Python/Go
SDKs. It supports stronger runtimes (gVisor, Kata Containers) via the pod
runtime class. It is installed from versioned release manifests; there is no
upstream Helm chart. Managed offerings (GKE Agent Sandbox) and production
users exist.

Maintaining a bespoke isolation layer in parallel would duplicate an upstream
primitive this kit does not differentiate on. The kit's differentiation is the
governance envelope around agent execution: approved-egress catalog, per-sandbox
budgets, the audit chain and evidence packs, and the control-framework map
(`platform/governance/control-framework-map.yaml`).

## Decision

Adopt kubernetes-sigs/agent-sandbox as an **optional, profile-gated workspace
runtime** underneath the existing governance envelope:

1. `deploy/charts/agent-workspace` gains a `sandbox.runtime` value:
   `namespace` (current behaviour, default, works on any conformant cluster)
   or `agent-sandbox` (renders a hardened `Sandbox` with an inline pod
   template and expects the agent-sandbox controller to be installed;
   `SandboxTemplate` and warm pools are reserved for a later pooled phase,
   since `v1beta1` claims allocate exclusively from pools).
2. The hardened profile sets `runtimeClassName` (gVisor or Kata where the
   cluster provides it, mirroring the optional `accelerator.runtimeClassName`
   pattern in the vLLM chart), disables service-account token automount,
   and stays compatible with the `restricted` Pod Security level.
3. The kit's NetworkPolicies and `ApprovedEgressCatalog` remain the
   **authoritative egress control** — on the direct-`Sandbox` path they are
   the *only* network control, since upstream creates no policy there. The
   catalog stays the only allow-path, `make egress-check` extends to
   sandbox-managed pods, and policy-as-code enforces that sandbox pods are
   selected by the default-deny policy.
4. The gateway's logical sandbox binds 1:1 to the runtime sandbox: the
   validated `X-Sandbox-ID` equals the `Sandbox` resource name and is carried
   on its labels (`platform.ai/sandbox-id`), so budgets, metrics, and audit
   events correlate with the workload that produced them.
5. Evidence packs (`make evidence`) gain an agent-sandbox readiness check
   (controller present, template renders, runtime class available), and the
   control-framework map gains an isolation control (working id `C-ISOLATE`):
   recommended at the `medium` risk tier, mandated at `high`.
6. Profiles: the local `kind` lab keeps `sandbox.runtime: namespace` as the
   default (no gVisor assumption on laptops); customer GPU and
   regulated-offline profiles document and default to `agent-sandbox`.

## Consequences

Positive:

- Kernel-level isolation becomes available for untrusted agent code and is
  tied to governance risk tiers instead of being uniformly absent.
- The isolation primitive is maintained by SIG Apps and its ecosystem
  (SDKs, warm pools, managed equivalents), not by this kit.
- The kit's story sharpens: governance, egress, budgets, and evidence over a
  standard primitive — the layer above agent-sandbox, not a competitor to it.
- Warm pools give a path to low-latency workspace allocation without bespoke
  pooling code.

Negative / accepted costs:

- New dependency on a `v1beta1` API that may still change before GA. Release
  manifests must be vendored and pinned in `docs/version-matrix.md`; upgrades
  go through the normal release-verification path.
- No upstream Helm chart: installation is by vendored versioned manifests,
  which is a second deployment mechanism next to Helm/Argo CD.
- gVisor/Kata become documented cluster prerequisites for the hardened
  profile (decision guide + version matrix); the local lab intentionally does
  not exercise them, so hardened-profile CI needs a capable environment.
- Two runtime paths (namespace, agent-sandbox) must be rendered and tested in
  `make validate`, increasing the chart test matrix.
- When the pooled path (SandboxTemplate/warm pools) is adopted, upstream's
  template-scoped NetworkPolicy model must be reconciled with the kit's
  default-deny policies so the egress catalog remains the single source of
  truth; this reconciliation is a design obligation, not an option.

## Alternatives considered

- **Keep namespace-only isolation (status quo).** Rejected as the terminal
  state: it offers no syscall-level barrier for model-generated code, which is
  unacceptable at the `high` risk tier. Retained as the default and local-lab
  profile — the envelope must keep working on any conformant cluster.
- **Build a kit-owned sandbox CRD and controller.** Rejected: duplicates a
  SIG Apps project with production adoption, adds permanent controller
  maintenance to a solo-maintained kit, and weakens the "governance layer over
  standard primitives" positioning.
- **Set `runtimeClassName` on plain workspace pods without agent-sandbox.**
  Considered as a minimal dependency-free hardening step. Loses lifecycle
  semantics (TTL, suspend/resume, claims, warm pools) and ecosystem
  compatibility. Kept as the documented fallback if upstream API churn ahead
  of GA proves too costly.
- **Hosted sandbox services (E2B, Daytona, Modal and similar).** Rejected:
  external SaaS execution of agent workloads contradicts the kit's
  local-first, sovereign scope (`docs/scope-and-non-goals.md`).
