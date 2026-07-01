# 0002. Policy engine: Kyverno

- Status: Accepted
- Date: 2026-07-01
- Deciders: Platform maintainer

## Context

The platform must enforce cluster-boundary guardrails at admission time: required ownership and cost
labels, non-root and locked-down security contexts, pinned image tags with resource requests and
limits, signed-image verification for the project's own images, a backstop against broad egress
CIDRs, and an encryption-at-rest attestation on PersistentVolumeClaims. These controls have to be
declarative (so they reconcile through Argo CD like everything else), testable in CI without a live
cluster, and approachable for a single maintainer to author and review.

## Decision

Use Kyverno as the admission policy engine.

- Policies are plain Kubernetes `ClusterPolicy` resources in
  [`deploy/policies/kyverno/policies.yaml`](../../deploy/policies/kyverno/policies.yaml):
  `ai-platform-required-labels`, `ai-platform-restricted-pods` (non-root, drop ALL capabilities,
  read-only root filesystem, disallow privileged), `ai-platform-image-and-resources` (block
  `:latest`, require requests and limits), `ai-platform-verify-project-images` (keyless `verifyImages`
  of `ghcr.io/ramazankara/private-ai-platform-kit/*`), `ai-platform-restrict-egress-cidrs`, and
  `ai-platform-pvc-encryption-at-rest`.
- Kyverno is installed as a platform operator via its upstream Helm chart, pinned in
  [`deploy/clusters/local/apps.yaml`](../../deploy/clusters/local/apps.yaml) (chart `kyverno`
  version `3.2.7`), and the policies ship as their own `security-policies` Argo CD application.
- Policies are unit-tested with Kyverno's own test harness under
  [`deploy/policies/kyverno/tests/`](../../deploy/policies/kyverno/tests/) (`kyverno-test.yaml` plus
  good/bad resource fixtures such as `privileged-pod.yaml`, `latest-pod.yaml`, and
  `unencrypted-pvc.yaml`), so policy behavior is verified in CI without a cluster.
- Failure actions are deliberate per policy: most are `Enforce`, the signed-image policy is
  `Enforce` with `background: false`, and the PVC encryption policy is `Audit` (surface unencrypted
  claims in policy reports until every storage class in use encrypts at rest).

## Consequences

- Policy authoring is in YAML that mirrors the resources it guards (`pattern`, `foreach`, `deny`
  conditions), so a maintainer reads a policy and the object it constrains in the same dialect.
- The signed-image policy turns the CI Cosign signing investment into a real runtime guarantee: only
  images keylessly signed by this repo's `ci.yml` workflow on `refs/heads/main` are admitted in
  non-infra namespaces. Forks that republish images must update `imageReferences` and the
  subject/issuer, which is a deliberate manual edit, not an auto-rewrite.
- Both engines and the rest of the platform are excluded from the restricted-pod and label policies
  by namespace (`kube-system`, `argocd`, `kyverno`, `monitoring`, and so on), so infrastructure is
  not blocked by tenant rules.
- Kyverno is an in-cluster admission dependency; if it is unhealthy, admission of new workloads in
  governed namespaces is affected. This is the intended fail-closed posture for `Enforce` policies.

## Alternatives considered

- **OPA Gatekeeper.** A mature CNCF policy controller with a large constraint-template ecosystem.
  Rejected as the default because authoring requires Rego, a separate language from the Kubernetes
  manifests it guards; Kyverno's Kubernetes-native YAML lowers the authoring and review cost for a
  single maintainer, and its built-in `verifyImages` covers the Cosign keyless verification this kit
  needs without a separate component.
- **Plain Pod Security Admission (PSA).** Built in and zero-dependency, and it would cover the
  non-root/privileged/read-only baseline. Rejected as sufficient on its own because PSA cannot
  express the label requirements, the pinned-image and resource rules, signed-image verification, the
  egress-CIDR backstop, or the PVC encryption attestation. PSA can still be layered underneath; the
  decision here is which engine carries the platform-specific rules.
- **Validating Admission Policy (CEL, in-tree).** Promising and dependency-free for validation, but
  it does not perform image signature verification and the rule set predates broad reliance on it.
  Kyverno consolidates validation and image verification in one already-pinned operator.
