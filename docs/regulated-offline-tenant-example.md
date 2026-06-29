# Regulated Offline Tenant Example

This walkthrough onboards a coding-agent tenant for a regulated, air-gapped team: confidential data
classification, **no external egress**, a private registry, and long evidence retention. It renders
from the reviewed spec
[tenants/onboarding/regulated-offline-coding-agents.yaml](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/tenants/onboarding/regulated-offline-coding-agents.yaml)
using the same onboarding flow as [Tenant labs](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/tenant-labs.md).

## What This Profile Enforces

| Control | Value | Effect |
| --- | --- | --- |
| `compliance.profile` | `regulated-offline` | Marks the namespace and contract as regulated/offline. |
| `compliance.dataClassification` | `confidential` | Stamped on the namespace and trace contract. |
| `compliance.externalEgressAllowed` | `false` | No external CIDR egress is rendered. |
| `network.allowedEgressCidrs` | `[]` | Egress is limited to in-cluster gateway, RAG, and DNS only. |
| `compliance.requirePrivateRegistry` | `true` | Images must come from the customer's private registry. |
| `compliance.evidenceRetentionDays` | `730` | Two-year evidence retention. |
| `agentWorkspace.rbac.allowJobManagement` | `false` | Agents cannot create or manage Jobs. |

The key difference from the default coding-agent profile is that `allowedEgressCidrs` is empty, so
the rendered NetworkPolicy permits only DNS and the in-cluster inference gateway and RAG service.

## Render The Tenant Artifacts

Validate the spec, then render namespace, quota, limit range, default-deny + allowlist
NetworkPolicies, the trace-contract ConfigMap, RBAC, and the agent workspace:

```bash
make tenant-onboard-regulated TENANT_OUTPUT=tenants/generated
```

This is equivalent to:

```bash
python3 scripts/tenant-onboard.py \
  --spec tenants/onboarding/regulated-offline-coding-agents.yaml \
  --output-dir tenants/generated
```

Review the generated manifests under `tenants/generated/` before applying. Confirm:

- the namespace carries `platform.ai/tenant`, `platform.ai/sandbox-id`, `pod-security.kubernetes.io/enforce=restricted`, and the confidential data-classification label;
- a default-deny NetworkPolicy exists with **no** egress allow rules beyond DNS, gateway, and RAG;
- the workspace `ServiceAccount` has a viewer-only Role (no job management).

## Apply And Verify

Apply through GitOps or `kubectl apply -f tenants/generated/`, then:

```bash
make tenant-smoke
```

Verify offline posture explicitly:

```bash
# From a pod in the tenant namespace, external egress must fail:
kubectl -n ai-regulated-agents run egress-test --rm -it --image=curlimages/curl --restart=Never -- \
  curl -m 5 https://example.com   # expected: timeout / blocked

# In-cluster gateway access must succeed:
kubectl -n ai-regulated-agents run gw-test --rm -it --image=curlimages/curl --restart=Never -- \
  curl -sS http://inference-gateway-inference-gateway.inference.svc.cluster.local:8080/healthz
```

## Customizing

- Tighten quotas (`spec.quotas`) and the limit range to the team's footprint.
- Set `storageClassName` on the agent workspace PVC to a customer-approved encrypted class.
- Keep `allowedEgressCidrs` empty for true offline; if a private mirror is unavoidable, add a single
  reviewed CIDR and record the approval, then re-render.
- Adjust `evidenceRetentionDays` to the customer's compliance requirement.

For the egress-allowed counterpart, see the
[GPU coding-agent tenant example](gpu-coding-agent-tenant-example.md).
