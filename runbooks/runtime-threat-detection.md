# Runtime Threat Detection Runbook

Admission control ([Kyverno](../deploy/policies/kyverno/policies.yaml)) and default-deny
NetworkPolicies decide *what is admitted* and *where pods may connect*. Neither observes what a pod
*does* after it starts. The threat model centers on indirect/RAG prompt injection and a hijacked
coding agent — post-exploitation behavior that preventive controls cannot see. This runbook covers
the optional detective layer that closes that gap.

## What It Detects

Falco (or Tetragon) watches syscalls/eBPF events and raises alerts on behavior such as:

- an unexpected shell spawned inside a runtime or agent pod,
- a reverse shell or outbound connection a workload never normally makes,
- reads of sensitive files (`/etc/shadow`, service-account tokens, mounted secrets),
- a crypto-miner or other unexpected binary executing,
- writes to normally read-only paths.

## Deploy (Opt-In)

Runtime detection is **not** part of the default GitOps sync because it runs a privileged/eBPF
DaemonSet, which the platform's own `disallow-privileged` Kyverno policy blocks in AI namespaces.

1. Deploy into a namespace **excluded** from the disallow-privileged policy. The Falco chart uses
   the `falco` namespace by default; confirm it is in the Kyverno exclusion set (alongside
   `monitoring`, `velero`, and the platform operators) before syncing, or add it.
2. Apply [`deploy/observability/runtime-security.yaml`](../deploy/observability/runtime-security.yaml)
   by adding it to your Argo CD root application or `kubectl apply -f` it directly.
3. Detections route through `falcosidekick` to Loki, so they land in the same log pipeline as the
   gateway/RAG redacted audit events and are queryable in Grafana.

## Respond

Treat a high-priority Falco alert on an `ai-agents`/`ai-sandbox` or runtime pod as a potential agent
hijack: capture the alert and pod, follow [incident-response.md](incident-response.md), and if
credential exposure is suspected, rotate per [oidc-jwks-rotation.md](oidc-jwks-rotation.md) and the
secret backend. Contain by scaling the workload to zero and preserving the pod for forensics before
deletion.

## Scope

The kit ships the integration and this runbook; the Falco/Tetragon install, rule tuning, and the
alert destination are operator-owned, consistent with the kit's boundary.
