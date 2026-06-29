# Tenant Lab Runbook

Use this runbook when creating a team-specific AI lab namespace for experiments, demos, or incident reproduction.

## What A Tenant Lab Gets

Each tenant lab namespace includes:

- restricted Pod Security labels
- required owner, cost-center, environment, tenant, and sandbox labels
- ResourceQuota and LimitRange
- default-deny ingress and egress NetworkPolicy
- explicit egress to kube-dns and the inference gateway
- trace-contract ConfigMap
- read-only Role and RoleBinding for a tenant group

The namespace sandbox id is also the value callers should send as `X-Sandbox-ID`.

## Create A Tenant

Create the default example tenant:

    make tenant-up

Create a custom tenant:

    TENANT_ID=team-b-lab TENANT_NAME=team-b TENANT_OWNER=team-b TENANT_GROUP=team-b make tenant-up

Run the smoke proof from inside the tenant namespace:

    TENANT_ID=team-b-lab make tenant-smoke

## Generate Customer Onboarding Artifacts

Use the reviewed onboarding spec when a customer needs a repeatable tenant package for both sandbox controls and coding-agent workspace values:

    make tenant-onboard

The default spec is `tenants/onboarding/coding-agents.yaml`. The generated files are written under `.out/tenants/<sandbox-id>/`:

- `tenant-lab.yaml` for Namespace, quota, LimitRange, NetworkPolicy, trace contract, Role, and RoleBinding
- `agent-workspace-values.yaml` for the `deploy/charts/agent-workspace` Helm chart
- `README.md` with apply commands for the tenant

Use a custom spec and output directory when onboarding a customer team:

    TENANT_SPEC=tenants/onboarding/coding-agents.yaml TENANT_OUTPUT=.out/tenants make tenant-onboard

Generate the regulated/offline profile when a team must run without external CIDR egress:

    make tenant-onboard-regulated

The regulated/offline spec is `tenants/onboarding/regulated-offline-coding-agents.yaml`. It adds compliance and data-classification labels, renders no external CIDR egress, disables default job-management RBAC, and records the compliance contract in the generated trace ConfigMap.

Review generated egress CIDRs, RBAC group names, quotas, PVC size, and storage class before applying the artifacts to a customer cluster.

## Inspect Controls

    kubectl get namespace ai-team-b-lab --show-labels
    kubectl -n ai-team-b-lab get resourcequota,limitrange,networkpolicy,configmap
    kubectl -n ai-team-b-lab logs job/tenant-trace-smoke

## Customer Adaptation

Customer clusters should keep the labels and trace contract but can tune quotas, RBAC subjects, and network allowlists to match their team and compliance boundaries. Keep default-deny egress unless the tenant has an approved dependency.
