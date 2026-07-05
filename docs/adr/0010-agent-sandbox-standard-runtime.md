# 0010. Agent-sandbox is the standard workspace runtime

- Status: Accepted
- Date: 2026-07-02
- Deciders: Ramazan Kara (maintainer)

## Context

ADR 0009 adopted kubernetes-sigs/agent-sandbox as an *optional, profile-gated*
workspace runtime behind a `sandbox.runtime` toggle, defaulting to the older
namespace-only path. That caution is inconsistent with the rest of the kit,
which is deliberately opinionated: Kyverno policies ship in `Enforce`,
namespaces are PSA-`restricted`, egress is default-deny, and images must be
signed. The one workload that executes model-generated code, the very reason
this platform has a threat model, defaulted to the weakest isolation offered,
and every optional path doubled the test matrix, the documentation, and the
adopter's decision burden.

The portability argument for the toggle proved weak in practice: the
controller installs from vendored, checksummed manifests in seconds on any
conformant cluster (verified on cgroup-v1 `kind`); only the kernel-isolation
*runtime class* (gVisor/Kata) is environment-dependent, and that remains a
separate, optional knob. The kit currently has no external users, so the cost
of removing the fallback is zero and the cost of keeping it is permanent.

Two adjacent duplications had accumulated around the same feature:

- `make agent-lab-up` installed the workspace with a manual Helm release,
  which now collides with the Argo CD-managed `agent-workspace` Application
  over resource ownership on any GitOps-managed cluster.
- `make sandbox-smoke` (the request-tracing check for the `ai-sandbox`
  namespace) shared a name with the new agent-sandbox runtime smoke while
  testing something unrelated; the Argo Application for that construct is
  already called `traceable-sandbox`.

## Decision

1. **The hardened agent-sandbox runtime is the only workspace runtime.** The
   `sandbox.runtime` toggle is removed; `deploy/charts/agent-workspace`
   always renders the hardened `Sandbox`. `sandbox.runtimeClassName` remains
   the only environment-dependent option. This amends decision points 1 and 6
   of ADR 0009; everything else in ADR 0009 stands.
2. **The short-lived projected workspace credential is on by default**
   (`workspace.credentials.projectedToken.enabled: true`), consistent with
   the kit's no-long-lived-secrets stance; it costs nothing where unused.
3. **The controller is a platform prerequisite**, installed as an
   `agent-sandbox-controller` Argo CD Application (server-side apply, early
   sync wave) in both cluster overlays, and by `make agent-sandbox-install`
   in the quickstart path.
4. **`make agent-lab-up` is removed.** GitOps owns the workspace instance;
   bare-cluster and demo installs use a documented one-line `helm upgrade
   --install`. `make agent-smoke` validates the deployed instance instead of
   installing a parallel one, and `make agent-sandbox-smoke` becomes
   validation-only for the same reason.
5. **`make sandbox-smoke` is renamed `make trace-smoke`** (script included)
   so "sandbox" unambiguously means the workspace runtime, matching the
   `traceable-sandbox` Application name.

## Consequences

- One runtime path: half the chart test matrix, no decision-guide fork, and
  the default posture equals the documented posture.
- Every conformant cluster must run the vendored controller; a cluster that
  cannot is no longer a supported workspace target (accepted: no such
  cluster is known, and the tracing/tenant features remain unaffected).
- `C-ISOLATE` stops being tier-conditional and is mandated at every risk
  tier; evidence packs fail when the controller is absent instead of
  recording an unclaimed control.
- Existing local labs need one Argo sync (the controller Application) or
  `make agent-sandbox-install` before the workspace Application converges.
- The RWO workspace PVC is now held by the long-lived sandbox pod; the
  `agent-smoke` Job shares it on the same node (fine on single-node labs;
  multi-node clusters should schedule them together or use RWX storage).

## Alternatives considered

- **Deprecation window (flip default, keep fallback one release).** The
  operationally mature choice for a project with users; rejected here as
  process theater: there are no users, and the fallback would still cost a
  doubled test matrix for a release cycle.
- **Keep the toggle indefinitely.** Rejected: permanent two-path tax and a
  default that contradicts the kit's security posture.
- **Also collapse the `platform` umbrella chart into GitOps-only.** Deferred:
  OCI chart distribution is a deliberate, separately recorded decision
  (ADR 0008) about how the kit is consumed, not an accident of caution.
