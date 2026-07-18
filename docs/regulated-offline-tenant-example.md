# Restricted-egress tenant example

The file `tenants/onboarding/regulated-offline-coding-agents.yaml` defines a tenant named `regulated-offline`. The name describes its intended use; the generated manifests enforce a narrower fact: pods in that tenant namespace receive no external CIDR egress rule.

This is not an air-gap configuration for the cluster. Image pulls, model downloads, GitOps, identity, and services outside the tenant namespace need separate offline controls.

## Render the manifests

```bash
make tenant-onboard-regulated TENANT_OUTPUT=.out/tenants
```

The renderer writes namespace, quota, limit range, NetworkPolicy, RBAC, trace-contract, and agent-workspace values under `.out/tenants`. Review the output before applying it.

The checked-in spec requests:

| Setting | Value |
| --- | --- |
| Data classification | `confidential` |
| External CIDR egress | disabled |
| Allowed in-cluster services | DNS, inference gateway, RAG service |
| Private registry required | `true` metadata flag |
| Evidence retention | 730 days metadata value |
| Job-management RBAC | disabled |

`requirePrivateRegistry` and `evidenceRetentionDays` are contract fields. They do not configure a registry, storage lifecycle, or retention job by themselves.

## Review before apply

Confirm that:

- the target namespace and sandbox ID are correct;
- the CNI actually enforces Kubernetes NetworkPolicy;
- no generated rule contains an external CIDR;
- the gateway and RAG namespace selectors match the target cluster;
- the workspace image is available from an internal registry;
- storage class, quota, and PVC size are appropriate;
- identity and API-key material are supplied without being written to Git.

Apply the reviewed files through the customer's GitOps process. A direct `kubectl apply` can be used in a disposable lab, but it is not the documented customer handoff path.

## Test the boundary

Use an image that is already present or available from the internal registry. From a pod in the tenant namespace, test both an allowed in-cluster destination and a destination that is otherwise reachable from the cluster. A timeout to an unroutable address does not prove that NetworkPolicy is working.

The repository's agent-sandbox smoke test uses the Kubernetes API as the deny target for this reason:

```bash
make agent-sandbox-smoke
```

To make the entire deployment offline, add private image/chart mirrors, preloaded model weights, internal Git and identity endpoints, DNS policy, cluster-wide egress rules, and an offline release-verification process.
