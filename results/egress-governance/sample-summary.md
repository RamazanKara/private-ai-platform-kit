# Sample Egress Governance Report

Generated: `2026-05-31T00:00:00Z`

Summary: 1 references checked, 0 errors.

| Source | Environment | CIDR | Ports | Catalog ref |
| --- | --- | --- | --- | --- |
| `tenants/onboarding/coding-agents.yaml` | customer | `203.0.113.0/24` | `443` | `customer-git-artifact-mirror-example` |

Use `make egress-check` to validate current references and `make egress-report` to write fresh evidence.
