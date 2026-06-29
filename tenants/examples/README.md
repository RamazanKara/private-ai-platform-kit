# Tenant Examples

Tracked examples:

| Example | Source Spec | Purpose |
| --- | --- | --- |
| `team-a-lab.yaml` | local sample | Small local tenant namespace controls. |
| Regulated offline team | `tenants/onboarding/regulated-offline-coding-agents.yaml` | Confidential coding-agent workspace with no external CIDR egress, private-registry requirement, and 730-day evidence retention. |
| GPU-backed coding-agent team | `tenants/onboarding/gpu-coding-agents.yaml` | Larger coding-agent workspace intended for customer clusters using a GPU-backed vLLM runtime profile. |

Generate full onboarding artifacts for either spec:

```bash
make tenant-onboard TENANT_SPEC=tenants/onboarding/regulated-offline-coding-agents.yaml TENANT_OUTPUT=.out/tenants
make tenant-onboard TENANT_SPEC=tenants/onboarding/gpu-coding-agents.yaml TENANT_OUTPUT=.out/tenants
```

Generated tenant artifacts stay under `.out/tenants/` and should be reviewed before applying to a cluster.
