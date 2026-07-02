# Agent Workspaces Runbook

Use this runbook when provisioning a controlled namespace for coding agents.

## What An Agent Workspace Gets

The `agent-workspace` chart creates:

- restricted namespace labels
- `platform.ai/workload-kind=coding-agent`
- ResourceQuota and LimitRange
- default-deny NetworkPolicy
- approved egress to kube-dns, inference gateway, RAG service, and optional customer CIDRs
- namespace-scoped ServiceAccount and RBAC
- workspace PVC
- `agent-platform-contract` ConfigMap with gateway URL, RAG URL, sandbox ID, and required headers

The workspace does not grant cluster-admin privileges and does not include runtime secrets. Customers should wire Git credentials, API keys, package mirrors, ticketing tools, or artifact stores through their own secret backend and explicit egress allowlists.

External egress must also be approved in `platform/network/egress-catalog.yaml` and referenced with `catalogRef`. Run `make egress-check` before applying a workspace that adds external CIDRs.

## Create A Workspace

Create the default local workspace:

    make agent-lab-up

Validate that a coding-agent-style pod can reach the gateway and RAG service:

    make agent-smoke

Inspect the contract:

    kubectl -n ai-agents get configmap agent-platform-contract -o yaml

## Hardened Runtime (agent-sandbox)

For `medium`/`high` risk tiers, run the workspace on the kubernetes-sigs/agent-sandbox
runtime (ADR 0009, `C-ISOLATE`) instead of a bare namespace. Install the vendored,
checksummed controller and enable the runtime:

    make agent-sandbox-install
    helm upgrade --install agent-workspace deploy/charts/agent-workspace \
      --namespace ai-agents \
      --values deploy/clusters/customer/values/agent-workspace.yaml \
      --set sandbox.runtime=agent-sandbox

Set `sandbox.runtimeClassName` (for example `gvisor`) where the cluster provides a
kernel-isolation runtime class; the Kyverno `ai-platform-hardened-sandboxes` policy
enforces the hardened pod template at admission. Validate the full contract —
hardening, short-lived projected credential, DNS positive control, and fail-closed
non-catalog egress:

    make agent-sandbox-smoke

Operational notes:

- The controller does **not** roll the singleton pod when the Sandbox pod template
  changes; delete the pod (`kubectl -n ai-agents delete pod <sandbox-id>`) and the
  controller recreates it from the current spec. The smoke does this automatically
  when it detects image or volume drift.
- Enable `workspace.credentials.projectedToken` to give agents a short-lived,
  audience-bound platform credential instead of long-lived secrets; the token path
  and audience appear in the `agent-platform-contract` ConfigMap, and the gateway
  verifies it via its JWT/JWKS settings.
- NetworkPolicy enforcement requires a policy-capable CNI; on kindnet the smoke
  reports non-enforcement instead of passing vacuously (see the threat model).
- For an end-to-end demonstration (real coding agent, allow/deny receipts on the
  audit chain, evidence pack), run `make agent-sandbox-demo`.

## Customer Adaptation

Edit `deploy/clusters/customer/values/agent-workspace.yaml` for quota, PVC size, tenant labels, and approved external CIDRs. Keep default-deny egress in place. Add only customer-approved Git hosts, package mirrors, artifact stores, or ticketing systems.

Example approved CIDR:

    networkPolicy:
      allowedEgressCidrs:
        - catalogRef: customer-git-artifact-mirror-example
          cidr: 203.0.113.0/24
          ports: [443]

## Troubleshooting

Check namespace controls:

    kubectl get namespace ai-agents --show-labels
    kubectl -n ai-agents get resourcequota,limitrange,networkpolicy,pvc,role,rolebinding

Check smoke evidence:

    kubectl -n ai-agents logs job/agent-platform-smoke

If a coding agent cannot reach a dependency, verify whether it is intentionally blocked by NetworkPolicy before widening egress.
