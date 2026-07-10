# Agent-Sandbox Integration Design

Status: Implemented ([ADR 0009](adr/0009-adopt-agent-sandbox-workspace-runtime.md), Accepted).
Since [ADR 0010](adr/0010-agent-sandbox-standard-runtime.md) the agent-sandbox
runtime is the **standard and only** workspace runtime: the `sandbox.runtime`
toggle described in the design below was removed, the projected workspace
credential is on by default, and the controller is a platform prerequisite.
Sections referring to the toggle or the namespace fallback are historical
design record.
Date: 2026-07-01 (amended 2026-07-02)

This document describes how the kit adopts
[kubernetes-sigs/agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox)
as an optional runtime for coding-agent workspaces, component by component, and
the delivery plan to ship it.

## Goals

- Offer kernel-level isolation (gVisor/Kata via runtime class) for agent
  workspaces at the `medium`/`high` governance risk tiers.
- Keep the kit's governance envelope (approved-egress catalog, per-sandbox
  budgets, audit chain, evidence packs) authoritative and unchanged in
  authority.
- Bind the platform's logical sandbox (`X-Sandbox-ID`) 1:1 to the runtime
  `Sandbox` resource so evidence correlates with the workload.
- Keep the local `kind` lab fully functional with no new prerequisites.

## Non-goals

- Replacing namespace isolation: the `agent-workspace` chart's namespace,
  RBAC, quota, and NetworkPolicy envelope stays for both runtimes.
- Wrapping or re-exposing the agent-sandbox API: consumers use upstream CRDs
  and SDKs directly inside the envelope the kit provisions.
- Multi-cluster sandbox scheduling, GPU-in-sandbox, or snapshot/restore
  workflows (revisit after upstream GA).

## Upstream summary

Verified against the vendored v0.5.0 CRD schemas and a live kind install on
2026-07-01 (M0).

- Core API group `agents.x-k8s.io/v1beta1` (storage version; `v1alpha1` still
  served): `Sandbox` only. Spec fields: `podTemplate` (`metadata` + `spec`,
  standard pod schema), `operatingMode` (`Running`|`Suspended`, default
  `Running`), `shutdownPolicy` (`Delete`|`Retain`, default `Retain`),
  `shutdownTime` (RFC 3339), `service` (bool; no Service is created by
  default), `volumeClaimTemplates`.
- Extensions API group `extensions.agents.x-k8s.io/v1beta1`:
  - `SandboxTemplate`: `podTemplate`, `networkPolicy` (`egress`/`ingress`
    rule arrays), `networkPolicyManagement`, `envVarsInjectionPolicy`,
    `service`, `volumeClaimTemplates` (+ `volumeClaimTemplatesPolicy`).
  - `SandboxClaim`: `warmPoolRef.name` (pool allocation only in `v1beta1`;
    the direct `sandboxTemplateRef` exists only in `v1alpha1`), `lifecycle`
    (`ttlSecondsAfterFinished`, `shutdownPolicy` incl. `DeleteForeground`,
    `shutdownTime`), `env`, `additionalPodMetadata`.
  - `SandboxWarmPool`: `replicas`, `sandboxTemplateRef.name`,
    `updateStrategy`.
- Consequence: on the **direct path** (create a `Sandbox` with an inline pod
  template) there is no template, and (verified live) **no upstream
  NetworkPolicy is created**. Upstream's secure-by-default networking is
  template-scoped. TTL cleanup is claim-scoped; a bare `Sandbox` is bounded
  with `shutdownTime`/`shutdownPolicy`.
- Controller: namespace `agent-sandbox-system`, Deployment
  `agent-sandbox-controller`, webhook Service. Install from versioned release
  manifests (no Helm chart), server-side apply required (CRD size). Vendored
  under `deploy/vendor/agent-sandbox/` with checksums; installed by
  `make agent-sandbox-install`.
- Live behaviour (kind, k8s v1.31.4): the sandbox pod is named exactly after
  the `Sandbox`, `podTemplate` labels propagate to the pod, the controller
  adds an `agents.x-k8s.io/sandbox-name-hash` selector label, and status
  reports `Ready` with `podIPs` and `nodeName`.
- Upgrade caveat (verified live): the controller does **not** roll the
  singleton pod when `spec.podTemplate` changes: the `Sandbox` object
  updates but the running pod keeps the old spec. Workspace template changes
  require deleting the pod (the controller recreates it from the current
  spec); `make agent-sandbox-smoke` does this automatically when it detects
  drift.

## Concept mapping

| Kit concept (today) | Upstream resource / mechanism |
| --- | --- |
| Workspace namespace (`ai-agents`) | Unchanged; `Sandbox` objects live inside it |
| `sandbox.id` chart value / `X-Sandbox-ID` header | `Sandbox` `metadata.name` (pod inherits the name) + `platform.ai/sandbox-id` label |
| Workspace pod (implicit, user-launched) | `Sandbox` pod managed by the controller |
| Chart hardening values | Hardened inline `podTemplate` on the rendered `Sandbox` (direct path) |
| PVC template in chart | `volumeClaimTemplates` on the sandbox |
| Manual teardown | `shutdownTime`/`shutdownPolicy` on `Sandbox`; `lifecycle.ttlSecondsAfterFinished` only on the pooled `SandboxClaim` path |
| (none) | `SandboxTemplate` + `SandboxWarmPool`/`SandboxClaim`: deferred pooled path |

## Component changes

### 1. `deploy/charts/agent-workspace`

- New value `sandbox.runtime: namespace | agent-sandbox` (default
  `namespace`). Update `values.schema.json` and run
  `make config-contract-update`.
- When `agent-sandbox`: render a `Sandbox` named `<sandbox.id>` (the pod
  inherits that name, so the gateway's `X-Sandbox-ID` binding becomes
  physical) with a hardened inline pod template:
  - `runtimeClassName` from a new `sandbox.runtimeClassName` value
    (empty by default, `gvisor` in customer profiles; same pattern as
    `accelerator.runtimeClassName` in the vLLM chart);
  - `automountServiceAccountToken: false`;
  - `securityContext` compatible with PSA `restricted` (non-root, no
    privilege escalation, all capabilities dropped, read-only root
    filesystem, seccomp `RuntimeDefault`);
  - workspace labels: `platform.ai/sandbox-id`,
    `platform.ai/traceable-sandbox: "true"` on the pod template (verified to
    propagate to the pod);
  - resources within the existing LimitRange defaults;
  - the chart PVC becomes `volumeClaimTemplates` on the sandbox;
  - a `shutdownTime`/`shutdownPolicy` option for bounded workspace lifetime
    (TTL-after-finished only exists on the pooled claim path).
- Opt-in short-lived platform credential
  (`workspace.credentials.projectedToken`, implemented 2026-07-01): a
  projected, audience-bound ServiceAccount token (min 600 s TTL,
  kubelet-rotated) mounted read-only at `/var/run/platform/token`, with the
  path and audience published in the `agent-platform-contract` ConfigMap. No
  Secret object and no long-lived credential enters the workspace, and the
  ambient SA token stays unmounted (Kyverno-enforced). The gateway verifies
  it with its existing JWT/JWKS auth: set `JWT_AUTH_ENABLED`,
  `JWT_JWKS_URL` (the cluster's service-account issuer JWKS, e.g.
  `https://kubernetes.default.svc.cluster.local/openid/v1/jwks`; grant the
  gateway issuer-discovery access where anonymous discovery is disabled),
  and `JWT_AUDIENCE=inference-gateway`. The local lab keeps API-key mode;
  JWT mode is the customer-profile path. Verified live by
  `make agent-sandbox-smoke` (JWT shape, audience, no ambient token).
- `SandboxTemplate` is **not** rendered on this path; it becomes relevant
  with warm pools (`SandboxWarmPool.spec.sandboxTemplateRef`), since
  `v1beta1` claims allocate exclusively from pools.
- The `agent-platform-contract` ConfigMap is unchanged: sandbox pods consume
  the same gateway/RAG URLs and required headers.

The following manifest was applied successfully on the M0 spike cluster
(busybox stand-in for the workspace image; pod ran, exec as uid 65532
worked, Sandbox reported `Ready`):

```yaml
apiVersion: agents.x-k8s.io/v1beta1
kind: Sandbox
metadata:
  name: agent-lab                          # == sandbox.id == X-Sandbox-ID
  namespace: ai-agents
  labels:
    platform.ai/sandbox-id: agent-lab
    platform.ai/traceable-sandbox: "true"
spec:
  podTemplate:
    metadata:
      labels:
        platform.ai/sandbox-id: agent-lab
    spec:
      runtimeClassName: gvisor            # customer profile only
      automountServiceAccountToken: false
      containers:
        - name: workspace
          image: "<agent runner image>"
          securityContext:
            runAsNonRoot: true
            runAsUser: 65532
            runAsGroup: 65532
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
            seccompProfile:
              type: RuntimeDefault
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 128Mi
```

### 2. Controller installation (GitOps)

- Vendor the upstream release manifests under
  `deploy/vendor/agent-sandbox/<version>/` and pin the version in
  `docs/version-matrix.md`.
- New Argo CD Application (customer profile) pointing at the vendored path;
  local lab installs via a new `make agent-sandbox-install` target.
- Upgrades follow `runbooks/release-verification.md`.

### 3. Egress governance (`platform/network`, Kyverno)

- Verified on the M0 spike: on the direct-`Sandbox` path upstream creates
  **no NetworkPolicy at all**. Its secure-by-default networking is
  template-scoped (`SandboxTemplate.spec.networkPolicy` +
  `networkPolicyManagement`). The kit's default-deny + catalog-allow
  policies are therefore the *only* network control on this path, not a
  second layer. They must select sandbox pods (label selector on
  `platform.ai/sandbox-id`), and the Kyverno check below is load-bearing.
  When the pooled path lands, set `networkPolicyManagement` so template
  policies cannot silently widen what the catalog allows.
- `scripts/egress-governance.py` / `make egress-check`: extend coverage to
  namespaces containing `Sandbox` resources; fail if a sandbox pod is not
  selected by the default-deny policy.
- New Kyverno policies + tests (`deploy/policies/kyverno/tests/`):
  - require `automountServiceAccountToken: false` in any `SandboxTemplate`;
  - require the hardened `securityContext`;
  - at `high` risk tier, require a non-empty `runtimeClassName`.

### 4. Gateway binding (`src/inference-gateway`)

- No change was needed for the binding itself: `X-Sandbox-ID` validation,
  budgets, and audit events already key on the sandbox id; the binding
  becomes physical by naming the `Sandbox` resource with the same id.
- **Receipts (implemented 2026-07-01).** Rather than a parallel
  `agent_action` event class (which would double-record every action), the
  existing chained audit events carry receipt semantics: `action_type`
  (`model_call` for `inference_request`/`batch_request`), `decision`
  (`allowed`, or `denied` with the reason in `error`, covering admission
  rejects, budget exhaustion, and guardrail blocks), and `guardrail_action`
  (the output-guardrail outcome when it fired). One action, one chained
  receipt; same hash chain, same redaction rules (fingerprints and counts,
  never raw content). Contract: `deploy/sandbox/base/trace-contract.yaml`;
  test: `test_audit_events_carry_agent_action_receipts`.
- Future action types (`tool_invocation`, `egress_attempt`,
  `workspace_exec`) require an emitter outside the sandbox trust boundary
  (open question 4) and are the implementation core of the
  auditable-agent-execution paper.

### 5. Evidence packs (`make evidence`)

- New check `agent-sandbox-readiness` (LIVE mode): controller Deployment
  ready, CRDs present at expected version, `SandboxTemplate` renders, runtime
  class present when the profile requires it, sandbox pods selected by
  default-deny policy.
- Evidence pack section: pinned upstream version (from the version matrix)
  and the isolation posture per workspace (runtime, runtimeClassName).

### 6. Governance crosswalk (`platform/governance`, `docs/ai-governance-crosswalk.md`)

- New control `C-ISOLATE`, "Agent workloads execute under kernel-level
  isolation with no ambient credentials":
  - NIST AI RMF: Manage;
  - EU AI Act: Art. 15 (accuracy, robustness and cybersecurity); the
    `agent_action` audit events extend the existing C-AUDIT mapping to
    Art. 12 (record-keeping) for agent execution;
  - ISO/IEC 42001: operational controls clause.
- Risk tiers: `medium`, recommended; `high`, mandated (enforced by the
  Kyverno tier policy above).

### 7. Docs

- This document under Explanation in `mkdocs.yml` nav; ADR 0009 row in
  `docs/adr/README.md` index (both files carry unrelated uncommitted edits as
  of writing; wire in when committing).
- `docs/decision-guide.md`: new row for when to enable the hardened runtime.
- `docs/version-matrix.md`: agent-sandbox version pin.
- `README.md` workspaces bullet: mention isolation via the upstream SIG
  primitive once the ADR is Accepted.

## Threat-model delta

Gains: syscall-level isolation between agent code and the node kernel
(gVisor/Kata); no ambient service-account token; metadata endpoints blocked by
two independent layers (upstream default + kit policy); TTL bounds the
lifetime of a compromised workspace.

Explicitly unchanged: prompt injection and tool-abuse remain application-layer
risks (mitigated by the gateway's guardrails, budgets, and the egress
catalog, not by the sandbox); data exfiltration through *approved* egress
destinations remains a governance decision recorded in the catalog; the audit
chain's wholesale-rewrite limitation still requires external head anchoring
(`runbooks/evidence-pack.md`).

Update `docs/threat-model.md` accordingly in M3.

## Delivery plan

Weekend-scoped milestones, target release v0.14.0:

- **M0: Spike. Done 2026-07-01.** Vendored v0.5.0 manifests with checksums
  (`deploy/vendor/agent-sandbox/`), added `make agent-sandbox-install` +
  `scripts/agent-sandbox-install.sh`, verified all four CRD schemas, and ran
  a hardened `Sandbox` end-to-end on a `kind` spike cluster
  (`agent-sandbox-spike`, node image v1.31.4 for cgroup-v1 hosts; same
  fallback as `local-up.sh`): pod `Running`, exec as uid 65532, status
  `Ready`. Corrections fed back into this document: extensions API group,
  claims are pool-only in `v1beta1`, no upstream NetworkPolicy on the
  direct path, TTL is claim-scoped.
- **M1: Chart runtime option. Done 2026-07-01.** `sandbox.runtime` +
  `sandbox.runtimeClassName` values render a hardened `Sandbox`
  (`deploy/charts/agent-workspace/templates/sandbox.yaml`); schema updated
  (no config-contract impact; contracts cover only the two services);
  Kyverno `ai-platform-hardened-sandboxes` policy with good/bad test
  resources (kyverno test: 11/11). Verified live: chart-managed sandbox
  Ready in ~9 s, uid 10001, read-only rootfs, no SA token, writable
  workspace PVC and /tmp scratch.
- **M2: Egress fail-closed. Done 2026-07-01.** Default-deny + approved
  egress select sandbox pods by construction (`podSelector: {}`), and
  `scripts/egress-governance.py` already scans the agent-workspace cluster
  values. `make agent-sandbox-smoke` proves the hardening contract, a DNS
  positive control, and a blocked probe to the otherwise reachable Kubernetes
  API, verified fail-closed on the default Calico `kind` cluster. The script
  rejects kindnet because it cannot produce valid NetworkPolicy evidence.
- **M3: Evidence & governance. Done 2026-07-01 (events deferred).**
  `C-ISOLATE` added to the control-framework map (recommended at `medium`,
  mandated at `high`) and the governance crosswalk; evidence pack gained a
  static asset check and a live controller check (verified against the
  spike cluster); threat model gained the isolation-boundary section.
  Deferred: the `agent_action` audit-event vocabulary (section 4) ships
  with the auditable-agent-execution paper work, where the untrusted-emitter
  question (open question 4) is resolved.
- **Addendum: standard runtime (ADR 0010). Done 2026-07-02.** The
  `sandbox.runtime` toggle was removed (the chart always renders the
  hardened Sandbox), the projected credential became default-on, the
  controller became a platform prerequisite (`agent-sandbox-controller`
  Application in both overlays + quickstart install), the `agent-lab-up`
  target was removed in favour of the GitOps-owned instance, the smokes
  became validation-only, and the `sandbox-smoke` target was renamed
  `trace-smoke`.
- **Addendum: receipts, credential broker, real-agent demo. Done
  2026-07-01.** The three deliberately-deferred pieces were implemented the
  same day: (1) agent-action receipt semantics on the gateway audit chain
  (section 4); (2) the projected-token workspace credential (section 1);
  (3) `make agent-sandbox-demo` runs aider inside the hardened sandbox on
  the full lab: allowed, denied, and real-agent receipts verified on the
  chain, with sandbox-id attribution via aider model settings
  (`extra_headers`). Verified operational caveats now handled by the
  scripts: singleton pods do not roll on template changes (drift detection
  + refresh), GitOps-managed namespaces are adopted rather than owned, and
  the local `qwen2.5:0.5b` CPU model is too small for aider's edit format.
  The demo reports this honestly; customer-profile models complete the
  task.
- **M4: Demo & docs. Done 2026-07-01 (release pending).**
  `make agent-sandbox-demo` composes controller install → hardened workspace
  → fail-closed exfiltration probe → evidence pack, and exercises the
  governed model path from inside the sandbox when the inference gateway is
  deployed (skips with guidance on a sandbox-only cluster). Decision guide,
  version matrix, chart README (generated), and CHANGELOG updated; full
  `make validate` green. Remaining for release: run the demo against the
  full lab (`make quickstart` first), update the top-level README workspaces
  bullet, cut v0.14.0, and flip ADR 0009 to Accepted.

## Open questions

1. ~~Exact v0.5.0 CRD schema for pod templates and label propagation.~~
   Resolved in M0. See "Upstream summary".
2. ~~Whether upstream's NetworkPolicy and the kit's default-deny compose.~~
   Narrowed in M0: no upstream policy exists on the direct path, so the
   question only applies to the pooled/template path
   (`networkPolicyManagement` semantics; verify enum values when that
   phase starts).
3. Warm pools: worth the resource cost on customer clusters, or defer until
   a real latency requirement appears? (Deferred by default.)
4. Where `agent_action` events for *in-workspace* activity (not gateway
   traffic) get emitted from without trusting the agent itself. Candidate:
   a sidecar-free node-level observer; out of scope for v0.14.0, relevant
   for the auditable-agent-execution paper.
5. Resolved: the local lab defaults to Calico and the blocked-exfiltration
   probe targets the reachable Kubernetes API; kindnet is rejected by the smoke.
