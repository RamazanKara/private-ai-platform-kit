# Agent-sandbox integration

Agent workspaces run as `agents.x-k8s.io/v1beta1` `Sandbox` resources managed by the
vendored `kubernetes-sigs/agent-sandbox` controller. This is the only workspace runtime in
release `v0.29.0`.

The decisions are recorded in [ADR 0009](adr/0009-adopt-agent-sandbox-workspace-runtime.md)
and [ADR 0010](adr/0010-agent-sandbox-standard-runtime.md). This page describes the current
contract rather than the implementation history.

## Installation

The controller manifests are vendored under `deploy/vendor/agent-sandbox/v0.5.0/` with
recorded SHA-256 checksums. Both Argo CD profiles contain an
`agent-sandbox-controller` application. The direct quickstart path installs the same files with:

```bash
make agent-sandbox-install
```

The install uses server-side apply because the CRDs are too large for the client-side
last-applied annotation.

## Workspace resource

`deploy/charts/agent-workspace` always renders one `Sandbox`. Its name is
`sandbox.id`, and the controller gives the managed pod the same name. The chart also creates:

- the workspace namespace, service account, RBAC, quota, and limit range;
- a writable PVC mounted at `/workspace`;
- a configuration map with the gateway and RAG service addresses;
- default-deny ingress and egress policies plus explicit DNS, gateway, RAG, and catalog-approved
  CIDR rules.

The pod template runs as UID/GID `10001`, uses `RuntimeDefault` seccomp, drops all Linux
capabilities, disables privilege escalation, and makes the root filesystem read-only. The
default Kubernetes service-account token is not mounted.

## Kernel isolation

The controller-managed pod is a lifecycle and policy boundary. For a separate kernel boundary, set
`sandbox.runtimeClassName` (empty in the checked-in local and customer values) to a cluster-provided runtime such as gVisor or Kata when that isolation is required, and
verify that the runtime exists on every node that may host a workspace.

## Platform credential

The chart projects a service-account token at `/var/run/platform/token`. It is scoped to the
`inference-gateway` audience, expires after 600 seconds by default, and is rotated by the kubelet.
The gateway only accepts it when JWT/JWKS verification is configured against the cluster issuer.

Audience binding and expiry confine the token to the gateway for a short window; handle it as a
credential like any other.

## Network boundary

The chart's NetworkPolicies are the network boundary for the direct `Sandbox` path. They run on a
CNI that enforces NetworkPolicy; the local profile uses Calico, and the smoke check requires an
enforcing CNI (kindnet is rejected as egress evidence).

Every external CIDR entry needs a `catalogRef` from
`platform/network/egress-catalog.yaml`. Approved destinations can receive data, so keep the
catalog narrow and review it as an exfiltration boundary.

## Updates and lifecycle

`workspace.shutdownTime` and `workspace.shutdownPolicy` can bound a workspace lifetime. They are
unset by default.

The v0.5.0 controller keeps its singleton pod across `Sandbox` pod template changes. After a chart
update, delete the managed pod so the controller recreates it from the current template. `make agent-sandbox-smoke` detects image or volume drift and performs that refresh.

## Validation

After the controller and workspace application are synced, run:

```bash
make agent-sandbox-smoke
```

The check verifies controller readiness, the `Sandbox` and pod state, non-root execution, the
read-only root filesystem, writable workspace storage, absence of the ambient token, the projected
token audience, working DNS, and blocked non-catalog egress.

`make agent-sandbox-demo` adds the governed model-call and audit-receipt walkthrough used by the
README animation.

## Operator configuration

- Select an isolation `RuntimeClass` to give the workspace its own kernel; without one it shares the node kernel.
- Enable the opt-in mTLS overlay to encrypt connections that NetworkPolicy allows.
- Scope credentials for approved external services separately from namespace RBAC.
- The gateway audit chain records governed model calls and reported agent actions; runtime detection
  (Falco/Tetragon) covers process and file activity inside the workspace.
- PVC availability, backup, retention, and secure deletion follow the cluster storage system.
