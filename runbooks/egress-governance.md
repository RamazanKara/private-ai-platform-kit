# Egress Governance Runbook

Use this runbook when adding external network access for coding-agent workspaces or tenant labs.

## Policy

Agent and tenant namespaces use default-deny networking. Egress to the inference gateway and RAG service is built in. Any external CIDR must be approved in `network/egress-catalog.yaml` and referenced from the tenant or workspace values with `catalogRef`.

## Add An External Destination

1. Add or update an approved catalog entry in `network/egress-catalog.yaml`.
2. Set the entry `status` to `approved`, define the owner, environments, expiry date, use cases, data classification, CIDRs, and ports.
3. Reference the entry from `tenants/onboarding/<tenant>.yaml` or `clusters/<environment>/values/agent-workspace.yaml`:

       allowedEgressCidrs:
         - catalogRef: customer-git-artifact-mirror-example
           cidr: 203.0.113.0/24
           ports: [443]
           description: customer-approved Git, artifact, or package mirror example

4. Run:

       make egress-check

5. Generate evidence:

       make egress-report

## Review Rules

Keep external egress narrow:

- Prefer customer-controlled mirrors over broad internet access.
- Use TLS ports unless a reviewed exception exists.
- Use one catalog entry per trust boundary.
- Keep `expiresOn` current and review entries before renewal.
- Do not add `0.0.0.0/0` or broad private ranges to coding-agent workspaces.

## Troubleshooting

If `make egress-check` fails, compare the requested `cidr`, `ports`, and environment against the catalog entry. The validator requires an approved, non-expired catalog entry whose destination exactly matches the requested network and ports.
